"""The reconciler -- the INVARIANT-1 backstop.

It exists for one failure the outbox categorically cannot cover. The outbox handles "we
decided to report and then died": the intent was durable. It cannot handle "we never got
as far as deciding" -- a worker killed mid-inference leaves a RUNNING ledger row with no
outbox entry and nothing to drain.

Being precise about the scope: lease expiry already returns an abandoned page to PENDING,
so a page *will* be retried without any help from here. The reconciler's genuinely unique
job is narrower -- it is the **deadline enforcer**, converting "retried forever" into
"reported failed exactly once".

``ai_engine_pages_total{outcome="force_failed"}`` should be **zero** in normal operation.
Any non-zero value means a worker died mid-page, which is why it is alerted on rather than
merely graphed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import CallbackKind, JobState, JobType, PageState
from ai_engine.domain.errors import ErrorCode, FailureReason
from ai_engine.domain.invariants import audit_pages
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import PAGES_TOTAL, Metrics
from ai_engine.ports.job_store import JobStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    jobs_examined: int
    pages_force_failed: int
    jobs_failed: int
    jobs_completed: int


class Reconciler:
    def __init__(
        self,
        *,
        store: JobStore,
        metrics: Metrics,
        job_deadline_seconds: int = 86_400,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._deadline = timedelta(seconds=job_deadline_seconds)

    async def run(self, jobs: list) -> ReconcileResult:
        """Sweep the supplied jobs. The caller decides which ones to examine."""
        now = datetime.now(UTC)
        force_failed = jobs_failed = jobs_completed = 0

        for job in jobs:
            if job.state is not JobState.RUNNING:
                continue
            updated = job.updated_at or now
            if now - updated < self._deadline:
                continue

            expected = _expected_pages(job)

            # A Job A that never reached metadata -> the Backend does not know pagesCount,
            # so the only coherent report is a whole-job failure.
            if expected is None:
                reason = FailureReason(
                    ErrorCode.PROCESSING_TIMEOUT,
                    "job exceeded its deadline before the page count was reported",
                )
                await self._store.enqueue_callback(
                    CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.JOB_FAILED, reason=reason)
                )
                await self._store.set_job_state(job.job_id, JobState.FAILED, reason=reason)
                jobs_failed += 1
                log.error("reconcile.job_failed", job_id=str(job.job_id))
                continue

            pages = await self._store.pages(job.job_id)
            audit = audit_pages(expected, pages)

            for page_number in set(audit.non_terminal) | set(audit.missing):
                reason = FailureReason(
                    ErrorCode.PROCESSING_TIMEOUT,
                    "page did not complete within the job deadline",
                )
                await self._store.enqueue_callback(
                    CallbackEnvelope(
                        job_id=job.job_id,
                        kind=CallbackKind.PAGE_FAILED,
                        page_number=page_number,
                        reason=reason,
                    )
                )
                try:
                    await self._store.set_page_state(
                        job.job_id, page_number, PageState.REPORTED_FAILED, reason=reason
                    )
                except KeyError:
                    # The row was never created. The callback still goes out, because the
                    # Backend is waiting for that page regardless of our bookkeeping.
                    log.error(
                        "reconcile.missing_ledger_row",
                        job_id=str(job.job_id),
                        page_number=page_number,
                    )
                force_failed += 1
                self._metrics.inc(
                    PAGES_TOTAL,
                    {"job_type": job.job_type.value, "outcome": "force_failed"},
                )
                log.error(
                    "page.force_failed",
                    job_id=str(job.job_id),
                    page_number=page_number,
                    detail="a worker died mid-page; the reconciler reported it",
                )

            await self._store.set_job_state(job.job_id, JobState.COMPLETED)
            jobs_completed += 1

        return ReconcileResult(
            jobs_examined=len(jobs),
            pages_force_failed=force_failed,
            jobs_failed=jobs_failed,
            jobs_completed=jobs_completed,
        )


def _expected_pages(job) -> tuple[int, ...] | None:
    """The exact pages the Backend is waiting on for this job, or None if unknowable.

    Job B carries its own page, so it is always knowable. Job A only becomes knowable once
    the page count has been established.
    """
    if job.job_type is JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION:
        return (job.page_number,) if job.page_number else None
    if job.pages_count is None:
        return None
    return tuple(range(1, job.pages_count + 1))
