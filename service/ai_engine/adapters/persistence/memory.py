"""In-memory JobStore.

Ships so the whole service is real and testable today. The Postgres adapter (T-4.1)
replaces it without touching the application layer, which only knows the
:class:`~ai_engine.ports.job_store.JobStore` port.

The atomicity contracts are honoured here rather than approximated, because the tests that
matter most -- idempotent accept, concurrent claims -- are only meaningful if the store
really is atomic:

* :meth:`accept` holds a lock across read-and-insert, mirroring
  ``INSERT ... ON CONFLICT (job_id) DO NOTHING``.
* :meth:`claim_job` / :meth:`claim_page` hand each row to exactly one caller, mirroring
  ``SELECT ... FOR UPDATE SKIP LOCKED``.
* :meth:`enqueue_callback` enforces one row per ``(job, page, kind)``, mirroring the
  ``one_callback_per_target`` unique constraint.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ai_engine.domain.callbacks import CallbackEnvelope, OutboxRecord
from ai_engine.domain.enums import ArtifactType, JobState, PageState
from ai_engine.domain.errors import FailureReason
from ai_engine.domain.job import Artifact, Job, PageTask
from ai_engine.ports.job_store import AcceptOutcome, TerminalPageError


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryJobStore:
    """Non-durable JobStore. Local development and tests only."""

    def __init__(self, *, lease_seconds: int = 300, callback_backoff_base: float = 2.0) -> None:
        self._jobs: dict[UUID, Job] = {}
        self._pages: dict[UUID, dict[int, PageTask]] = {}
        self._artifacts: dict[tuple[UUID, int, ArtifactType], Artifact] = {}
        self._outbox: dict[int, OutboxRecord] = {}
        self._outbox_keys: dict[tuple, int] = {}
        self._next_outbox_id = 1
        self._lease_seconds = lease_seconds
        # Exposed so tests can shrink the retry window; production uses the default.
        self._callback_backoff_base = callback_backoff_base
        self._lock = asyncio.Lock()

    # ── Jobs ──────────────────────────────────────────────────────────────────────

    async def accept(self, job: Job) -> AcceptOutcome:
        async with self._lock:
            existing = self._jobs.get(job.job_id)
            if existing is not None:
                # Same id, same payload -> a Backend retry. REQ-IDEM-01: return 202.
                # Same id, different payload -> a caller bug we surface rather than hide,
                # because a 202 would silently drop the new instruction.
                return AcceptOutcome(
                    job=existing,
                    created=False,
                    conflict=existing.payload_hash != job.payload_hash,
                )
            now = _now()
            job.created_at = now
            job.updated_at = now
            self._jobs[job.job_id] = job
            self._pages.setdefault(job.job_id, {})
            return AcceptOutcome(job=job, created=True)

    async def get(self, job_id: UUID) -> Job | None:
        return self._jobs.get(job_id)

    async def claim_job(self, worker: str) -> Job | None:
        """Hand the oldest claimable job to exactly one caller."""
        async with self._lock:
            now = _now()
            for job in sorted(self._jobs.values(), key=lambda j: j.created_at or now):
                if job.state is JobState.QUEUED:
                    pass
                elif job.state is JobState.RUNNING and self._lease_expired(job, now):
                    # The previous worker is presumed dead. Reclaim rather than strand.
                    pass
                else:
                    continue
                if job.attempt >= 100:  # runaway guard; real cap is JOB_MAX_ATTEMPTS
                    continue
                job.state = JobState.RUNNING
                job.attempt += 1
                job.claimed_by = worker
                job.lease_until = now + timedelta(seconds=self._lease_seconds)
                job.updated_at = now
                return job
            return None

    def _lease_expired(self, job: Job, now: datetime) -> bool:
        return job.lease_until is None or job.lease_until <= now

    async def set_job_state(
        self,
        job_id: UUID,
        state: JobState,
        *,
        reason: FailureReason | None = None,
        pages_count: int | None = None,
        metadata_sent: bool = False,
        defer_seconds: float | None = None,
    ) -> Job:
        async with self._lock:
            job = self._jobs[job_id]
            job.state = state
            job.updated_at = _now()
            if defer_seconds is not None:
                # See the port docstring: holding the lease is the deferral mechanism.
                job.lease_until = _now() + timedelta(seconds=defer_seconds)
            if pages_count is not None:
                job.pages_count = pages_count
            if metadata_sent:
                job.metadata_sent_at = _now()
            if reason is not None:
                job.failure_code = reason.code
                job.failure_reason = reason.to_wire()
            return job

    # ── Page ledger ───────────────────────────────────────────────────────────────

    async def pages(self, job_id: UUID) -> list[PageTask]:
        ledger = self._pages.get(job_id, {})
        return [ledger[n] for n in sorted(ledger)]

    async def fan_out_pages(self, job_id: UUID, pages_count: int) -> list[PageTask]:
        if pages_count < 1:
            raise ValueError(f"pages_count must be >= 1, got {pages_count}")
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"unknown job {job_id}")
            ledger = self._pages.setdefault(job_id, {})
            for number in range(1, pages_count + 1):
                ledger.setdefault(number, PageTask(job_id=job_id, page_number=number))
            job.pages_count = pages_count
            job.updated_at = _now()
            return [ledger[n] for n in sorted(ledger)]

    async def ensure_page(self, job_id: UUID, page_number: int) -> PageTask:
        """Create exactly one ledger row.

        Job B covers a single supplied page, which may be page 7 of a book -- so it needs
        one row numbered 7, not rows 1..7.
        """
        if page_number < 1:
            raise ValueError(f"page_number must be >= 1, got {page_number}")
        async with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"unknown job {job_id}")
            ledger = self._pages.setdefault(job_id, {})
            return ledger.setdefault(page_number, PageTask(job_id=job_id, page_number=page_number))

    async def claim_page(self, job_id: UUID) -> PageTask | None:
        async with self._lock:
            now = _now()
            ledger = self._pages.get(job_id, {})
            for number in sorted(ledger):
                page = ledger[number]
                claimable = page.state is PageState.PENDING or (
                    page.state is PageState.RUNNING
                    and (page.lease_until is None or page.lease_until <= now)
                )
                if not claimable:
                    continue
                page.state = PageState.RUNNING
                page.attempt += 1
                page.started_at = page.started_at or now
                page.lease_until = now + timedelta(seconds=self._lease_seconds)
                return page
            return None

    async def set_page_state(
        self,
        job_id: UUID,
        page_number: int,
        state: PageState,
        *,
        reason: FailureReason | None = None,
    ) -> PageTask:
        async with self._lock:
            page = self._pages.setdefault(job_id, {}).get(page_number)
            if page is None:
                raise KeyError(f"unknown page {job_id}/{page_number}")
            if page.is_terminal and not state.is_terminal:
                # Terminal is absorbing. The page has already had its final word with the
                # Backend, so re-running it could only produce a contradictory second
                # report -- which is exactly what INVARIANT-1 forbids.
                raise TerminalPageError(
                    f"page {job_id}/{page_number} is already {page.state.value} and "
                    "cannot be returned to a non-terminal state"
                )
            page.state = state
            if state.is_terminal:
                page.terminal_at = _now()
                page.lease_until = None
            if reason is not None:
                page.failure_code = reason.code
                page.failure_reason = reason.to_wire()
            return page

    # ── Artifact ledger ───────────────────────────────────────────────────────────

    async def record_artifact(self, artifact: Artifact) -> None:
        key = (artifact.job_id, artifact.page_number, artifact.artifact_type)
        async with self._lock:
            # Idempotent: the first confirmed upload wins, so a retried page does not
            # replace a good record with a duplicate one.
            self._artifacts.setdefault(key, artifact)

    async def artifacts_for(self, job_id: UUID, page_number: int) -> dict[ArtifactType, Artifact]:
        return {
            artifact_type: artifact
            for (j, p, artifact_type), artifact in self._artifacts.items()
            if j == job_id and p == page_number
        }

    # ── Callback outbox ───────────────────────────────────────────────────────────

    async def enqueue_callback(self, envelope: CallbackEnvelope) -> OutboxRecord:
        async with self._lock:
            key = envelope.dedupe_key
            existing_id = self._outbox_keys.get(key)
            if existing_id is not None:
                # One row per logical callback. A page task that re-runs cannot produce a
                # second `completed` for the same page.
                return self._outbox[existing_id]
            record = OutboxRecord(
                id=self._next_outbox_id, envelope=envelope, next_attempt_at=_now()
            )
            self._outbox[record.id] = record
            self._outbox_keys[key] = record.id
            self._next_outbox_id += 1
            return record

    async def pending_callbacks(self, limit: int = 100) -> list[OutboxRecord]:
        now = _now()
        ready = [
            record
            for record in self._outbox.values()
            if not record.delivered
            and (record.next_attempt_at is None or record.next_attempt_at <= now)
        ]
        return sorted(ready, key=lambda r: r.id)[:limit]

    async def mark_callback_delivered(self, record_id: int) -> None:
        async with self._lock:
            self._outbox[record_id].delivered_at = _now()

    async def mark_callback_failed(self, record_id: int, error: str) -> None:
        async with self._lock:
            record = self._outbox[record_id]
            record.attempt += 1
            record.last_error = error
            # Exponential backoff with a cap; jitter is applied by the drainer.
            delay = min(self._callback_backoff_base**record.attempt, 600)
            record.next_attempt_at = _now() + timedelta(seconds=delay)

    async def running_jobs(self) -> list[Job]:
        """Jobs the reconciler should examine."""
        return [j for j in self._jobs.values() if j.state is JobState.RUNNING]

    async def outbox_records(self) -> list[OutboxRecord]:
        """Test and diagnostic helper: every record, delivered or not."""
        return sorted(self._outbox.values(), key=lambda r: r.id)

    async def clear(self) -> None:
        async with self._lock:
            self._jobs.clear()
            self._pages.clear()
            self._artifacts.clear()
            self._outbox.clear()
            self._outbox_keys.clear()
            self._next_outbox_id = 1
