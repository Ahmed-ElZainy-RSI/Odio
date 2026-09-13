"""Job A orchestration: PAGES_IMG_AND_TEXT_EXTRACTION.

The step order is fixed by the V1 spec and every step is a gate on the next:

    fetch source -> count pages -> PATCH metadata {pagesCount}
      -> create the page ledger -> run pages -> one terminal report each

`metadata` before any page work is not a stylistic choice: the spec says the Backend wants
the page count "before start text and image extraction", and sending page callbacks for a
job whose page count the Backend does not know is incoherent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ai_engine.application.run_page_task import PageOutcome, PageTaskExecutor, PageTaskSpec
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import CallbackKind, JobState
from ai_engine.domain.errors import (
    AiEngineError,
    CallbackFailedError,
    ErrorCode,
    FailureReason,
    SourceInvalidError,
)
from ai_engine.domain.invariants import audit_completion
from ai_engine.domain.job import Job
from ai_engine.observability.context import bind
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.backend import SourceFetcher
from ai_engine.ports.job_store import JobStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JobOutcome:
    state: JobState
    pages_count: int | None
    pages: tuple[PageOutcome, ...] = ()
    error_code: ErrorCode | None = None


class RunExtractionJob:
    """Drives one Job A to a terminal state."""

    def __init__(
        self,
        *,
        store: JobStore,
        fetcher: SourceFetcher,
        renderer: object,
        page_executor: PageTaskExecutor,
        metrics: Metrics,
        scratch_root: Path,
        flush_callbacks: Callable[[], Awaitable[object]] | None = None,
        max_pages: int = 500,
        page_concurrency: int = 4,
    ) -> None:
        self._store = store
        self._fetcher = fetcher
        self._renderer = renderer
        self._pages = page_executor
        self._metrics = metrics
        self._scratch_root = scratch_root
        self._flush_callbacks = flush_callbacks
        self._max_pages = max_pages
        self._page_concurrency = page_concurrency

    async def __call__(self, job: Job) -> JobOutcome:
        with bind(job_id=str(job.job_id), job_type=job.job_type.value):
            scratch = self._scratch_root / str(job.job_id)
            try:
                return await self._run(job, scratch)
            except AiEngineError as exc:
                return await self._fail_job(job, exc.reason)
            except BaseException as exc:
                reason = FailureReason(
                    ErrorCode.INTERNAL_ERROR,
                    f"unexpected {type(exc).__name__} while running the job",
                )
                log.error("job.unexpected_error", exc_info=exc)
                return await self._fail_job(job, reason)

    async def _run(self, job: Job, scratch: Path) -> JobOutcome:
        # 1. Fetch. A failure here is job-level: the spec names "you cant parse or get the
        #    pdf" as the whole-job failure case, and we do not even know pagesCount yet.
        source = await self._fetcher.fetch(
            source_download_url=job.source_download_url,
            file_id=job.file_id,
            destination_dir=str(scratch),
        )
        self._renderer.verify(source.local_path)  # type: ignore[attr-defined]

        # 2. Count pages.
        pages_count = self._renderer.page_count(source.local_path)  # type: ignore[attr-defined]
        if pages_count < 1:
            raise SourceInvalidError("the PDF reports zero pages")
        if pages_count > self._max_pages:
            raise SourceInvalidError(
                f"PDF has {pages_count} pages, exceeding the {self._max_pages}-page limit"
            )
        await self._store.set_job_state(job.job_id, JobState.RUNNING, pages_count=pages_count)

        # 3. Report the count, and WAIT for it to land. This is a gate, not a
        #    notification: the spec wants the count "before start text and image
        #    extraction", and sending page callbacks for a job whose page count the
        #    Backend does not know is incoherent.
        await self._store.enqueue_callback(
            CallbackEnvelope(
                job_id=job.job_id,
                kind=CallbackKind.METADATA,
                pages_count=pages_count,
            )
        )
        log.info("job.metadata_enqueued", pages_count=pages_count)
        await self._await_metadata_delivery(job)

        # 4. Create every ledger row up front, so no page can be forgotten by omission.
        # Resolve the OCR engine now that the document is on disk: `auto` inspects the
        # PDF's text layer, and a misconfigured paid provider must fail this job once
        # rather than once per page.
        self._pages.prepare(str(source.local_path))

        await self._store.fan_out_pages(job.job_id, pages_count)

        # 5. Run the pages that still need work. A resumed job must skip pages that have
        #    already had their final word with the Backend: re-running one would repeat
        #    paid AI work, and its second terminal callback would be a contradictory
        #    report -- which is why the outbox and the store both refuse it. Skipping here
        #    is what makes the refusal a non-event rather than an error path.
        ledger = {page.page_number: page for page in await self._store.pages(job.job_id)}
        pending = [
            number
            for number in range(1, pages_count + 1)
            if not (ledger.get(number) and ledger[number].is_terminal)
        ]
        already_done = pages_count - len(pending)
        if already_done:
            log.info(
                "job.resumed",
                pages_count=pages_count,
                already_reported=already_done,
                remaining=len(pending),
            )

        semaphore = asyncio.Semaphore(self._page_concurrency)

        async def run_one(page_number: int) -> PageOutcome:
            async with semaphore:
                return await self._pages.run(
                    PageTaskSpec(
                        job=job,
                        page_number=page_number,
                        source_path=source.local_path,
                        scratch_dir=scratch / f"p{page_number:03d}",
                    )
                )

        outcomes = await asyncio.gather(*(run_one(n) for n in pending))

        # 6. If a provider allowance ran out mid-book, the remaining pages are parked, not
        #    failed. Defer the whole job until the window resets and let the next claim
        #    resume it; the pages that did report keep their reports, and the reconciler's
        #    deadline is still the backstop if the allowance never returns.
        parked = [outcome for outcome in outcomes if outcome.parked]
        if parked:
            retry_after = max(outcome.retry_after_seconds for outcome in parked)
            await self._store.set_job_state(
                job.job_id, JobState.RUNNING, defer_seconds=max(retry_after, 1.0)
            )
            log.warning(
                "job.deferred",
                reason="provider budget exhausted",
                parked_pages=len(parked),
                reported_pages=pages_count - len(parked),
                retry_after_seconds=round(retry_after, 1),
            )
            return JobOutcome(
                state=JobState.RUNNING,
                pages_count=pages_count,
                pages=tuple(outcomes),
                error_code=ErrorCode.RATE_LIMITED,
            )

        # 7. Verify INVARIANT-1 before declaring the job complete.
        audit = audit_completion(pages_count, await self._store.pages(job.job_id))
        if not audit.is_complete:
            log.error("job.invariant_violation", detail=audit.describe())
            reason = FailureReason(
                ErrorCode.INTERNAL_ERROR,
                f"pages did not all report: {audit.describe()}",
            )
            return await self._fail_job(job, reason)

        await self._store.set_job_state(job.job_id, JobState.COMPLETED)
        log.info(
            "job.completed",
            pages_count=pages_count,
            processed_now=len(outcomes),
            succeeded=sum(1 for o in outcomes if o.succeeded),
            failed=sum(1 for o in outcomes if not o.succeeded),
        )
        return JobOutcome(state=JobState.COMPLETED, pages_count=pages_count, pages=tuple(outcomes))

    async def _await_metadata_delivery(self, job: Job) -> None:
        """Drain the outbox until the metadata callback has actually been delivered.

        If it cannot be delivered, no page work starts and the job escalates to a
        whole-job failure -- which is the honest outcome, because the Backend has no page
        count to reconcile page reports against.
        """
        if self._flush_callbacks is None:
            # No drainer wired (unit tests exercising the stages in isolation). Trust the
            # enqueue and move on rather than deadlocking.
            return
        for _ in range(3):
            await self._flush_callbacks()
            refreshed = await self._store.get(job.job_id)
            if refreshed is not None and refreshed.metadata_sent_at is not None:
                job.metadata_sent_at = refreshed.metadata_sent_at
                log.info("job.metadata_delivered")
                return
        raise CallbackFailedError(
            "could not deliver the page count to the Backend, so no page work was started"
        )

    async def _fail_job(self, job: Job, reason: FailureReason) -> JobOutcome:
        """Whole-job failure. No page callbacks are sent -- REQ-CB-05."""
        await self._store.enqueue_callback(
            CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.JOB_FAILED, reason=reason)
        )
        await self._store.set_job_state(job.job_id, JobState.FAILED, reason=reason)
        log.error("job.failed", error_code=reason.code.value, detail=reason.detail)
        return JobOutcome(
            state=JobState.FAILED, pages_count=job.pages_count, error_code=reason.code
        )
