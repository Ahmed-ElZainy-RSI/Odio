"""The Page Task Executor -- the unit INVARIANT-1 is defined over.

    For a job with pagesCount = N, deliver exactly one terminal callback for every page
    in 1..N, or fail the whole job.

This module is the reason that holds. Its contract is narrow and absolute: **every call
results in exactly one terminal page state and exactly one enqueued terminal callback.**

The existing pipeline gets this wrong in the same way in five places -- a bare
``except Exception: print(...); break`` that abandons every remaining page
(``src/pipeline/run_ocr_gemini.py:59-62``, ``run_speak_gemini.py:79-84``). Here the
try/except is the outermost thing in the function and it catches ``BaseException``,
because an unhandled error escaping a page task is exactly what strands a book.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ai_engine.artifacts.text_builder import build_text_artifact
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import ARTIFACT_ORDER, ArtifactType, CallbackKind, PageState
from ai_engine.domain.errors import (
    PROGRAMMING_ERRORS,
    AiEngineError,
    ErrorCode,
    FailureReason,
    ProcessingTimeoutError,
    RateLimitedError,
)
from ai_engine.domain.job import Artifact, ArtifactDescriptor, Job
from ai_engine.observability.context import bind
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import PAGES_TOTAL, Metrics
from ai_engine.ports.backend import ArtifactUploader
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PageTaskSpec:
    """Everything a page task needs. Carries no HTTP object and no DB session."""

    job: Job
    page_number: int
    source_path: str
    scratch_dir: Path


@dataclass(frozen=True, slots=True)
class PageOutcome:
    page_number: int
    state: PageState
    error_code: ErrorCode | None = None
    retry_after_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.state is PageState.REPORTED_COMPLETED

    @property
    def parked(self) -> bool:
        """Not done, and deliberately not reported either.

        A page is parked when a provider allowance ran out. Reporting it failed would be
        a lie -- nothing was tried -- and retrying immediately would burn attempts against
        a wall, so the page stays PENDING and the job is deferred until the window resets.
        """
        return self.state is PageState.PENDING


class PageTaskExecutor:
    """Runs one page of Job A to exactly one terminal report."""

    def __init__(
        self,
        *,
        store: JobStore,
        renderer: object,
        ocr: object | None = None,
        uploader: ArtifactUploader,
        metrics: Metrics,
        page_timeout_seconds: float | None = 900.0,
        ocr_for: Callable[[str], object] | None = None,
        budget: RateBudget | None = None,
    ) -> None:
        self._store = store
        self._renderer = renderer
        # `auto` engine selection depends on whether *this* PDF has a text layer, which is
        # only knowable after the source is fetched -- so the engine is resolved per job
        # rather than at composition time. A fixed instance still works for tests.
        if ocr is None and ocr_for is None:
            raise ValueError("provide either an ocr engine or an ocr_for factory")
        self._ocr_for = ocr_for or (lambda _path: ocr)
        self._engines: dict[str, object] = {}
        self._budget = budget
        self._uploader = uploader
        self._metrics = metrics
        # A wall-clock deadline turns a hang into a reported failure. Without one, a stuck
        # page holds a worker slot indefinitely while the Backend waits for that page --
        # and there is no such deadline anywhere in the legacy pipeline.
        self._page_timeout = page_timeout_seconds

    def prepare(self, source_path: str) -> object:
        """Resolve (and cache) the OCR engine for one document.

        Called by the job orchestrator before page fan-out so an unusable engine -- a paid
        provider with no API key, say -- fails the job once, instead of failing every page
        individually after paying to render each one.
        """
        cached = self._engines.get(source_path)
        if cached is None:
            cached = self._ocr_for(source_path)
            self._engines[source_path] = cached
        return cached

    async def run(self, spec: PageTaskSpec) -> PageOutcome:
        job_id = spec.job.job_id
        page = spec.page_number

        with bind(job_id=str(job_id), page_number=page):
            try:
                if self._page_timeout is None:
                    return await self._run_stages(spec)
                async with asyncio.timeout(self._page_timeout):
                    return await self._run_stages(spec)
            except TimeoutError:
                reason = FailureReason(
                    ErrorCode.PROCESSING_TIMEOUT,
                    f"page did not complete within {self._page_timeout:.0f}s",
                )
                log.error("page.timed_out", timeout_seconds=self._page_timeout)
                return await self._report_failed(job_id, page, reason)
            except RateLimitedError as exc:
                return await self._park(job_id, page, exc)
            except ProcessingTimeoutError as exc:
                return await self._report_failed(job_id, page, exc.reason)
            except AiEngineError as exc:
                return await self._report_failed(job_id, page, exc.reason)
            except PROGRAMMING_ERRORS as exc:
                # Adapters let these through on purpose (spec 007): a bug in our code, not a
                # provider outage. Still exactly one terminal report, but say what it is.
                reason = FailureReason(
                    ErrorCode.INTERNAL_ERROR,
                    f"internal error ({type(exc).__name__}) in the engine's own code",
                )
                log.error("page.programming_error", exc_info=exc)
                return await self._report_failed(job_id, page, reason)
            except BaseException as exc:
                # An unanticipated error must still produce a terminal report, otherwise
                # the Backend waits for this page forever.
                reason = FailureReason(
                    ErrorCode.INTERNAL_ERROR,
                    f"unexpected {type(exc).__name__} while processing the page",
                )
                log.error("page.unexpected_error", exc_info=exc)
                return await self._report_failed(job_id, page, reason)

    # ── Stages ────────────────────────────────────────────────────────────────────

    async def _run_stages(self, spec: PageTaskSpec) -> PageOutcome:
        """Produce, upload, report. Wrapped by the deadline in :meth:`run`.

        Rendering and OCR are synchronous CPU work, so they run in a thread to keep the
        event loop responsive and, crucially, to let the deadline actually fire -- a
        blocking call in the loop cannot be cancelled.
        """
        # Charge the provider budget *before* doing any work: the point is to avoid
        # walking into a quota wall, and a refusal here costs one cheap DB round trip
        # instead of a render plus a rejected paid call.
        engine = self.prepare(spec.source_path)
        if self._budget is not None:
            await self._budget.acquire(getattr(engine, "provider", "local"))

        descriptors = await asyncio.to_thread(self._produce, spec)
        file_ids = await self._upload_in_order(spec, descriptors)
        return await self._report_completed(spec.job.job_id, spec.page_number, file_ids)

    def _produce(self, spec: PageTaskSpec) -> dict[ArtifactType, ArtifactDescriptor]:
        """Render the page image and build the page text. Both artifacts, in memory."""
        page = spec.page_number
        image = self._renderer.render_page(spec.source_path, page)  # type: ignore[attr-defined]
        engine = self.prepare(spec.source_path)
        tokens, _provenance = engine.extract_page(  # type: ignore[attr-defined]
            page_image=image, pdf_path=spec.source_path, page_number=page
        )
        text_artifact = build_text_artifact(tokens, page)
        image_artifact = ArtifactDescriptor(
            artifact_type=ArtifactType.IMAGE,
            payload=image,
            file_name=f"p{page:03d}.jpg",
        )
        return {ArtifactType.IMAGE: image_artifact, ArtifactType.TEXT: text_artifact}

    async def _upload_in_order(
        self, spec: PageTaskSpec, descriptors: dict[ArtifactType, ArtifactDescriptor]
    ) -> dict[ArtifactType, str]:
        """Upload image then text (REQ-UPL-05), skipping anything already confirmed."""
        job_id = spec.job.job_id
        page = spec.page_number
        already = await self._store.artifacts_for(job_id, page)
        file_ids: dict[ArtifactType, str] = {
            kind: artifact.backend_file_id for kind, artifact in already.items()
        }

        for artifact_type in ARTIFACT_ORDER[spec.job.job_type]:
            if artifact_type in file_ids:
                # A retried page must not re-upload what already landed, and must not
                # double-confirm it.
                log.info("artifact.already_confirmed", artifact_type=artifact_type.value)
                continue
            descriptor = descriptors[artifact_type]
            backend_file_id = await self._uploader.upload(
                descriptor=descriptor,
                folder_id=spec.job.folder_id,
                path_prefix=spec.job.path_prefix,
            )
            # The ledger row is written only AFTER confirm-upload succeeds, so a partial
            # upload leaves no false record.
            await self._store.record_artifact(
                Artifact(
                    job_id=job_id,
                    page_number=page,
                    artifact_type=artifact_type,
                    backend_file_id=backend_file_id,
                    size_in_bytes=descriptor.size_in_bytes,
                )
            )
            file_ids[artifact_type] = backend_file_id

        return file_ids

    async def _report_completed(
        self, job_id: UUID, page: int, file_ids: dict[ArtifactType, str]
    ) -> PageOutcome:
        await self._store.set_page_state(job_id, page, PageState.ARTIFACTS_UPLOADED)
        job = await self._store.get(job_id)
        assert job is not None
        ordered = ARTIFACT_ORDER[job.job_type]
        # Enqueue, do not send. The intent must be durable before this process can die.
        await self._store.enqueue_callback(
            CallbackEnvelope(
                job_id=job_id,
                kind=CallbackKind.PAGE_COMPLETED,
                page_number=page,
                artifacts=tuple((kind, file_ids[kind]) for kind in ordered),
            )
        )
        await self._store.set_page_state(job_id, page, PageState.REPORTED_COMPLETED)
        self._metrics.inc(PAGES_TOTAL, {"job_type": job.job_type.value, "outcome": "completed"})
        log.info("page.completed")
        return PageOutcome(page_number=page, state=PageState.REPORTED_COMPLETED)

    async def _park(self, job_id: UUID, page: int, exc: RateLimitedError) -> PageOutcome:
        """Leave the page PENDING with no callback at all.

        This is the one path that ends a page attempt without a terminal report, and it is
        safe precisely because it does not *end* the page: the ledger row stays PENDING, so
        INVARIANT-1 is still owed and still enforced -- by the job deferral below, and
        ultimately by the reconciler's deadline, which fails the job for real if the
        allowance never comes back.
        """
        await self._store.set_page_state(job_id, page, PageState.PENDING)
        log.warning(
            "page.parked",
            reason="provider budget exhausted",
            retry_after_seconds=round(exc.retry_after_seconds, 1),
        )
        return PageOutcome(
            page_number=page,
            state=PageState.PENDING,
            error_code=exc.reason.code,
            retry_after_seconds=exc.retry_after_seconds,
        )

    async def _report_failed(self, job_id: UUID, page: int, reason: FailureReason) -> PageOutcome:
        await self._store.enqueue_callback(
            CallbackEnvelope(
                job_id=job_id,
                kind=CallbackKind.PAGE_FAILED,
                page_number=page,
                reason=reason,
            )
        )
        await self._store.set_page_state(job_id, page, PageState.REPORTED_FAILED, reason=reason)
        job = await self._store.get(job_id)
        job_type = job.job_type.value if job else "unknown"
        self._metrics.inc(PAGES_TOTAL, {"job_type": job_type, "outcome": "failed"})
        log.warning("page.failed", error_code=reason.code.value, detail=reason.detail)
        return PageOutcome(
            page_number=page, state=PageState.REPORTED_FAILED, error_code=reason.code
        )
