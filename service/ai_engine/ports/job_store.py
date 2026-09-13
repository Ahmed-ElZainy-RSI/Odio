"""Persistence port.

The application layer talks only to this. Two adapters are planned: an in-memory one
(shipped, used by the API and the tests) and a Postgres one (T-4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from ai_engine.domain.callbacks import CallbackEnvelope, OutboxRecord
from ai_engine.domain.enums import ArtifactType, JobState, PageState
from ai_engine.domain.errors import FailureReason
from ai_engine.domain.job import Artifact, Job, PageTask


class TerminalPageError(RuntimeError):
    """Raised when something tries to un-terminate a page.

    A page that has reported to the Backend is finished. Re-running it could only produce a
    second, contradictory report, so the stores refuse rather than allow it.
    """


@dataclass(frozen=True, slots=True)
class AcceptOutcome:
    """Result of an idempotent insert-if-absent.

    ``created`` False with ``conflict`` False means the same jobId arrived again with the
    same payload -- REQ-IDEM-01's "if already enqueued return 202 accepted".
    ``conflict`` True means the same jobId arrived with a *different* payload, which is a
    caller bug we surface rather than hide.
    """

    job: Job
    created: bool
    conflict: bool = False


@runtime_checkable
class JobStore(Protocol):
    """Durable job and page state.

    REQ-JOB-05 and REQ-IDEM-02 require a store keyed by job GUID, so this port is not
    optional infrastructure -- it is a requirement.
    """

    async def accept(self, job: Job) -> AcceptOutcome:
        """Insert the job if its id is unused; otherwise return the existing one.

        MUST be atomic. Two concurrent calls with the same ``job_id`` must produce exactly
        one ``created=True`` outcome -- the Postgres adapter does this with a single
        ``INSERT ... ON CONFLICT (job_id) DO NOTHING``, not a check-then-insert.
        """
        ...

    async def get(self, job_id: UUID) -> Job | None: ...

    async def pages(self, job_id: UUID) -> list[PageTask]:
        """The page ledger for a job, ordered by page number. Empty before fan-out."""
        ...

    async def fan_out_pages(self, job_id: UUID, pages_count: int) -> list[PageTask]:
        """Create one ledger row per page, transactionally, BEFORE any page work starts.

        Creating every row up front is what makes it impossible to forget a page by
        omission: afterwards the only question is whether each row reached a terminal
        state, never whether it exists.
        """
        ...

    async def ensure_page(self, job_id: UUID, page_number: int) -> PageTask:
        """Create exactly one ledger row, for a single-page job."""
        ...

    async def claim_job(self, worker: str) -> Job | None:
        """Take the next queued job, or None. Must be safe for concurrent workers."""
        ...

    async def claim_page(self, job_id: UUID) -> PageTask | None:
        """Take the next pending page of a job, or None."""
        ...

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
        """Move a job to ``state``.

        ``defer_seconds`` pushes the lease that far into the future, which makes the job
        invisible to :meth:`claim_job` until then. That is how a job parked behind an
        exhausted provider budget waits for its window to reset without spinning: no new
        queue, no new state, just a lease that has not expired yet.
        """
        ...

    async def set_page_state(
        self,
        job_id: UUID,
        page_number: int,
        state: PageState,
        *,
        reason: FailureReason | None = None,
    ) -> PageTask: ...

    async def record_artifact(self, artifact: Artifact) -> None:
        """Record a CONFIRMED upload. Idempotent on (job, page, artifact_type)."""
        ...

    async def artifacts_for(self, job_id: UUID, page_number: int) -> dict[ArtifactType, Artifact]:
        """Already-confirmed artifacts, so a retried page skips what already landed."""
        ...

    async def running_jobs(self) -> list[Job]:
        """Jobs in RUNNING state, for the reconciler to examine."""
        ...

    async def enqueue_callback(self, envelope: CallbackEnvelope) -> OutboxRecord:
        """Store a callback intent. At most one row per (job, page, kind)."""
        ...

    async def pending_callbacks(self, limit: int = 100) -> list[OutboxRecord]: ...

    async def mark_callback_delivered(self, record_id: int) -> None: ...

    async def mark_callback_failed(self, record_id: int, error: str) -> None: ...
