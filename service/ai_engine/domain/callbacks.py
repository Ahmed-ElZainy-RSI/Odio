"""Callback envelopes and the outbox record.

Progress callbacks are written to a durable outbox in the SAME transaction as the state
change that caused them, then delivered by a separate drainer.

Why that matters, concretely: consider *upload image -> upload text -> send completed*. If
the callback were a bare in-line HTTP call and the worker died between the last
`confirm-upload` and the PATCH, then the artifacts would exist in the Backend's storage,
the page would genuinely be done, and **nothing would ever tell the Backend so**. The lease
would expire, the page would be retried, and a retry that finds both artifacts already
confirmed could reasonably conclude the page is finished and never send the callback. The
book stalls forever.

That window is a few hundred milliseconds and it occurs on every deploy. The outbox removes
it by making the *intent to call* durable before the process can die.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ai_engine.domain.enums import ArtifactType, CallbackKind
from ai_engine.domain.errors import FailureReason


@dataclass(frozen=True, slots=True)
class CallbackEnvelope:
    """One pending outbound progress call."""

    job_id: UUID
    kind: CallbackKind
    page_number: int | None = None
    pages_count: int | None = None
    artifacts: tuple[tuple[ArtifactType, str], ...] = ()
    reason: FailureReason | None = None

    def __post_init__(self) -> None:
        if self.kind in _PAGE_SCOPED and self.page_number is None:
            raise ValueError(f"{self.kind} requires a page_number")
        if self.kind not in _PAGE_SCOPED and self.page_number is not None:
            raise ValueError(f"{self.kind} must not carry a page_number")
        if self.kind is CallbackKind.METADATA and self.pages_count is None:
            raise ValueError("metadata callback requires pages_count")
        if self.kind is CallbackKind.PAGE_COMPLETED and len(self.artifacts) != 2:
            raise ValueError(
                "a completed callback carries exactly two artifacts "
                f"(image+text or audio+json), got {len(self.artifacts)}"
            )
        if self.kind in _REASON_REQUIRED and self.reason is None:
            raise ValueError(f"{self.kind} requires a reason")

    @property
    def is_page_terminal(self) -> bool:
        """Whether this envelope is a page's final word to the Backend."""
        return self.kind in _PAGE_SCOPED

    @property
    def dedupe_key(self) -> tuple[UUID, int | None, str]:
        """At most one row per logical callback.

        For page-terminal kinds the key deliberately collapses `page_completed` and
        `page_failed` into one slot. Keying on the *kind* would allow a page to be reported
        failed and then completed, which is just as much a double report from the
        Backend's point of view as two `completed` calls -- their page bookkeeping would be
        wrong either way. A page gets exactly one final word.
        """
        if self.is_page_terminal:
            return (self.job_id, self.page_number, "page_terminal")
        return (self.job_id, self.page_number, self.kind.value)

    def path(self) -> str:
        """The Backend path this envelope targets."""
        match self.kind:
            case CallbackKind.METADATA:
                return f"/api/jobs/{self.job_id}/metadata"
            case CallbackKind.PAGE_COMPLETED:
                return f"/api/jobs/{self.job_id}/{self.page_number}/completed"
            case CallbackKind.PAGE_FAILED:
                return f"/api/jobs/{self.job_id}/{self.page_number}/failed"
            case CallbackKind.JOB_FAILED:
                return f"/api/jobs/{self.job_id}/failed"
        raise AssertionError(f"unhandled kind {self.kind}")  # pragma: no cover

    def body(self) -> dict[str, Any]:
        """The exact JSON body the V1 contract expects."""
        match self.kind:
            case CallbackKind.METADATA:
                return {"pagesCount": self.pages_count}
            case CallbackKind.PAGE_COMPLETED:
                return {
                    "artifacts": [
                        {"fileId": file_id, "artifactType": artifact_type.value}
                        for artifact_type, file_id in self.artifacts
                    ]
                }
            case CallbackKind.PAGE_FAILED | CallbackKind.JOB_FAILED:
                assert self.reason is not None  # guaranteed by __post_init__
                return {"reason": self.reason.to_wire()}
        raise AssertionError(f"unhandled kind {self.kind}")  # pragma: no cover


_PAGE_SCOPED = frozenset({CallbackKind.PAGE_COMPLETED, CallbackKind.PAGE_FAILED})
_REASON_REQUIRED = frozenset({CallbackKind.PAGE_FAILED, CallbackKind.JOB_FAILED})


@dataclass(slots=True)
class OutboxRecord:
    """A stored envelope plus its delivery bookkeeping."""

    id: int
    envelope: CallbackEnvelope
    attempt: int = 0
    delivered_at: datetime | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None

    @property
    def delivered(self) -> bool:
        return self.delivered_at is not None
