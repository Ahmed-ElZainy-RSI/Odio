"""Job B orchestration: PAGE_AUDIO_AND_TEXT_TIMING_GENERATION.

Job B is one job covering exactly **one** page, so it differs from Job A in two ways that
matter:

* It sends **no** `metadata` callback -- there is no page count to report.
* Its single page callback is also its completion signal, so INVARIANT-1 degenerates to
  "send exactly one callback".

The step order:

    fetch the page's free text -> tokenize -> synthesize -> align
      -> build the timing artifact -> upload audio, then json -> report the page

The tokenize step is the hinge back to Job A. The `Text` artifact is a space-join of tokens
that may not contain whitespace, so `str.split()` inverts it losslessly and the ids
reproduce exactly -- no shared state, no correlation table.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path

from ai_engine.artifacts.timing_builder import build_timing_artifact
from ai_engine.artifacts.tokenizer import tokenize
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import ARTIFACT_ORDER, ArtifactType, CallbackKind, JobState, PageState
from ai_engine.domain.errors import (
    AiEngineError,
    ErrorCode,
    FailureReason,
    RateLimitedError,
    SourceInvalidError,
)
from ai_engine.domain.job import Artifact, ArtifactDescriptor, Job
from ai_engine.domain.tokens import PageProvenance
from ai_engine.observability.context import bind
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import ALIGNMENT_ANCHOR_RATIO, PAGES_TOTAL, Metrics
from ai_engine.ports.backend import ArtifactUploader, SourceFetcher
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AudioJobOutcome:
    state: JobState
    page_number: int
    error_code: ErrorCode | None = None
    anchor_ratio: float | None = None


class RunAudioJob:
    """Drives one Job B to a terminal state."""

    def __init__(
        self,
        *,
        store: JobStore,
        fetcher: SourceFetcher,
        tts: object,
        aligner: object,
        uploader: ArtifactUploader,
        metrics: Metrics,
        scratch_root: Path,
        voice: str | None = None,
        page_timeout_seconds: float | None = 900.0,
        flush_callbacks: object | None = None,
        budget: RateBudget | None = None,
    ) -> None:
        self._store = store
        self._fetcher = fetcher
        self._tts = tts
        self._aligner = aligner
        self._uploader = uploader
        self._metrics = metrics
        self._scratch_root = scratch_root
        self._voice = voice
        self._page_timeout = page_timeout_seconds
        self._flush_callbacks = flush_callbacks
        self._budget = budget

    async def __call__(self, job: Job) -> AudioJobOutcome:
        page = job.page_number
        assert page is not None, "Job B always carries a pageNumber"

        with bind(job_id=str(job.job_id), job_type=job.job_type.value, page_number=page):
            # One ledger row for THIS page -- numbered `page`, not 1..page. Job B may be
            # asked for page 7 of a book, and `fan_out_pages` would create rows 1..7.
            # No pages_count either: Job B has no page-count concept and reports none.
            await self._store.ensure_page(job.job_id, page)
            await self._store.set_job_state(job.job_id, JobState.RUNNING)
            try:
                if self._page_timeout is None:
                    return await self._run(job, page)
                async with asyncio.timeout(self._page_timeout):
                    return await self._run(job, page)
            except TimeoutError:
                return await self._fail_page(
                    job,
                    page,
                    FailureReason(
                        ErrorCode.PROCESSING_TIMEOUT,
                        f"page did not complete within {self._page_timeout:.0f}s",
                    ),
                )
            except RateLimitedError as exc:
                # Parked, not failed: nothing was attempted, so no terminal report is
                # owed yet. The job keeps its lease until the allowance returns.
                await self._store.set_page_state(job.job_id, page, PageState.PENDING)
                await self._store.set_job_state(
                    job.job_id,
                    JobState.RUNNING,
                    defer_seconds=max(exc.retry_after_seconds, 1.0),
                )
                log.warning(
                    "page.parked",
                    reason="provider budget exhausted",
                    retry_after_seconds=round(exc.retry_after_seconds, 1),
                )
                return AudioJobOutcome(
                    state=JobState.RUNNING, page_number=page, error_code=exc.reason.code
                )
            except AiEngineError as exc:
                return await self._fail_page(job, page, exc.reason)
            except BaseException as exc:
                log.error("audio_job.unexpected_error", exc_info=exc)
                return await self._fail_page(
                    job,
                    page,
                    FailureReason(
                        ErrorCode.INTERNAL_ERROR,
                        f"unexpected {type(exc).__name__} while generating audio",
                    ),
                )

    async def _run(self, job: Job, page: int) -> AudioJobOutcome:
        scratch = self._scratch_root / str(job.job_id)

        # 1. Fetch the page's free text.
        source = await self._fetcher.fetch(
            source_download_url=job.source_download_url,
            file_id=job.file_id,
            destination_dir=str(scratch),
        )
        raw = Path(source.local_path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceInvalidError("page text is not valid UTF-8") from exc

        # 2. Reproduce Job A's token ids from the text alone.
        tokens = tokenize(text, page)
        if not tokens:
            raise SourceInvalidError("page text contained no tokens")
        log.info("audio_job.tokenized", tokens=len(tokens))

        # 3. Synthesize, then 4. align. These stay in one task: alignment consumes the
        #    audio bytes TTS just produced, so splitting them would ship ~1 MB per hop.
        # Charge the TTS provider's budget before spending it. Hume's quota running out
        # mid-book is what left `work/المومياوات ناشيونال جيوجرافيك` with 33 page records
        # and zero audio; parking is how that becomes "later" instead of "never".
        if self._budget is not None:
            await self._budget.acquire(getattr(self._tts, "provider", "local"))
        audio, audio_format, duration_ms = await asyncio.to_thread(
            self._tts.synthesize, text, voice=self._voice
        )
        timed, report = await asyncio.to_thread(
            self._aligner.align,
            audio=audio,
            audio_format=audio_format,
            duration_ms=duration_ms,
            tokens=tokens,
        )
        self._metrics.observe(
            ALIGNMENT_ANCHOR_RATIO,
            report.anchor_ratio,
            {
                "align_method": getattr(self._aligner, "method", "unknown"),
                "tts_model": getattr(self._tts, "model_id", "unknown"),
            },
        )

        # 5. Build both artifacts.
        provenance = PageProvenance(
            tts_model=getattr(self._tts, "model_id", None),
            voice=self._voice,
            align_method=getattr(self._aligner, "method", None),
        )
        descriptors = {
            ArtifactType.AUDIO: ArtifactDescriptor(
                artifact_type=ArtifactType.AUDIO,
                payload=audio,
                file_name=f"p{page:03d}.wav",
            ),
            ArtifactType.JSON: build_timing_artifact(
                page_number=page,
                tokens=timed,
                duration_ms=duration_ms,
                audio_format=audio_format,
                bind_report=report,
                provenance=provenance,
            ),
        }

        # 6. Upload audio, then json (REQ-UPL-05), skipping anything already confirmed.
        file_ids = await self._upload_in_order(job, page, descriptors)

        # 7. Report the page. Enqueued, not sent: the intent must be durable first.
        await self._store.set_page_state(job.job_id, page, PageState.ARTIFACTS_UPLOADED)
        await self._store.enqueue_callback(
            CallbackEnvelope(
                job_id=job.job_id,
                kind=CallbackKind.PAGE_COMPLETED,
                page_number=page,
                artifacts=tuple((kind, file_ids[kind]) for kind in ARTIFACT_ORDER[job.job_type]),
            )
        )
        await self._store.set_page_state(job.job_id, page, PageState.REPORTED_COMPLETED)
        await self._store.set_job_state(job.job_id, JobState.COMPLETED)
        self._metrics.inc(PAGES_TOTAL, {"job_type": job.job_type.value, "outcome": "completed"})
        log.info(
            "audio_job.completed",
            duration_ms=duration_ms,
            anchor_ratio=round(report.anchor_ratio, 4),
        )
        return AudioJobOutcome(
            state=JobState.COMPLETED, page_number=page, anchor_ratio=report.anchor_ratio
        )

    async def _upload_in_order(
        self, job: Job, page: int, descriptors: dict[ArtifactType, ArtifactDescriptor]
    ) -> dict[ArtifactType, str]:
        already = await self._store.artifacts_for(job.job_id, page)
        file_ids: dict[ArtifactType, str] = {
            kind: artifact.backend_file_id for kind, artifact in already.items()
        }
        for artifact_type in ARTIFACT_ORDER[job.job_type]:
            if artifact_type in file_ids:
                log.info("artifact.already_confirmed", artifact_type=artifact_type.value)
                continue
            descriptor = descriptors[artifact_type]
            backend_file_id = await self._uploader.upload(
                descriptor=descriptor,
                folder_id=job.folder_id,
                path_prefix=job.path_prefix,
            )
            await self._store.record_artifact(
                Artifact(
                    job_id=job.job_id,
                    page_number=page,
                    artifact_type=artifact_type,
                    backend_file_id=backend_file_id,
                    size_in_bytes=descriptor.size_in_bytes,
                )
            )
            file_ids[artifact_type] = backend_file_id
        return file_ids

    async def _fail_page(self, job: Job, page: int, reason: FailureReason) -> AudioJobOutcome:
        """One terminal page report, then the job is done.

        Job B's page callback *is* its completion signal, so the job reaches COMPLETED even
        when the page failed -- matching REQ-META-02's "whether they fail or succeed".
        """
        await self._store.enqueue_callback(
            CallbackEnvelope(
                job_id=job.job_id,
                kind=CallbackKind.PAGE_FAILED,
                page_number=page,
                reason=reason,
            )
        )
        with contextlib.suppress(KeyError):
            # The ledger row is created up front, so this should always exist; suppressing
            # is belt and braces so a bookkeeping gap can never block the callback.
            await self._store.set_page_state(
                job.job_id, page, PageState.REPORTED_FAILED, reason=reason
            )
        await self._store.set_job_state(job.job_id, JobState.COMPLETED)
        self._metrics.inc(PAGES_TOTAL, {"job_type": job.job_type.value, "outcome": "failed"})
        log.warning("audio_job.page_failed", error_code=reason.code.value, detail=reason.detail)
        return AudioJobOutcome(state=JobState.COMPLETED, page_number=page, error_code=reason.code)
