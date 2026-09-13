"""Job, page and artifact entities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from ai_engine.domain.enums import ArtifactType, JobState, JobType, PageState, QualityTier
from ai_engine.domain.errors import ErrorCode


def canonical_payload_hash(
    *,
    job_type: JobType,
    file_id: str,
    folder_id: str,
    path_prefix: str,
    page_number: int | None,
) -> str:
    """Stable hash used to detect a re-used jobId carrying a *different* payload.

    `sourceDownloadUrl` is deliberately EXCLUDED: a presigned URL legitimately changes
    between retries -- REQ-SRC-02 exists precisely because it expires -- so including it
    would turn every ordinary Backend retry into a spurious 409.
    """
    material = json.dumps(
        {
            "jobType": job_type.value,
            "fileId": file_id,
            "folderId": folder_id,
            "pathPrefix": path_prefix,
            "pageNumber": page_number,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Job:
    """One unit of work as accepted from the Backend."""

    job_id: UUID
    job_type: JobType
    file_id: str
    folder_id: str
    path_prefix: str
    source_download_url: str
    payload_hash: str
    page_number: int | None = None
    quality_tier: QualityTier = QualityTier.FAST
    state: JobState = JobState.QUEUED
    pages_count: int | None = None
    metadata_sent_at: datetime | None = None
    attempt: int = 0
    claimed_by: str | None = None
    lease_until: datetime | None = None
    failure_code: ErrorCode | None = None
    failure_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.job_type.requires_page_number and self.page_number is None:
            raise ValueError(f"{self.job_type} requires a pageNumber")
        if not self.job_type.requires_page_number and self.page_number is not None:
            raise ValueError(f"{self.job_type} must not carry a pageNumber")

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    @property
    def may_start_page_work(self) -> bool:
        """REQ-META-01 gate: no page work until `pagesCount` has been reported.

        Job B carries its own page and sends no metadata, so it is never gated.
        """
        if self.job_type.requires_page_number:
            return True
        return self.metadata_sent_at is not None


@dataclass(slots=True)
class PageTask:
    """One page's unit of work. The thing INVARIANT-1 is quantified over."""

    job_id: UUID
    page_number: int
    state: PageState = PageState.PENDING
    attempt: int = 0
    lease_until: datetime | None = None
    failure_code: ErrorCode | None = None
    failure_reason: str | None = None
    started_at: datetime | None = None
    terminal_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError(f"page_number must be >= 1, got {self.page_number}")

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal


@dataclass(frozen=True, slots=True)
class Artifact:
    """A file that has been uploaded AND confirmed with the Backend.

    A row exists only after `confirm-upload` returns 200, which is what makes a retried
    page skip work that already landed.
    """

    job_id: UUID
    page_number: int
    artifact_type: ArtifactType
    backend_file_id: str
    size_in_bytes: int
    content_type: str = ""

    def __post_init__(self) -> None:
        if self.size_in_bytes <= 0:
            raise ValueError("size_in_bytes must be positive")
        expected = self.artifact_type.content_type
        if self.content_type and self.content_type != expected:
            raise ValueError(f"{self.artifact_type} must be {expected}, got {self.content_type}")
        object.__setattr__(self, "content_type", self.content_type or expected)


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """An artifact that is ready to upload but has no Backend file id yet."""

    artifact_type: ArtifactType
    payload: bytes
    file_name: str
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def content_type(self) -> str:
        return self.artifact_type.content_type

    @property
    def size_in_bytes(self) -> int:
        return len(self.payload)

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError(f"{self.artifact_type} artifact payload is empty")
        if not self.file_name:
            raise ValueError("file_name must not be empty")
