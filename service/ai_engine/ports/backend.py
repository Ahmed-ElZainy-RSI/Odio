"""Ports for everything the Backend team owns, plus Keycloak and object storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from ai_engine.domain.enums import ArtifactType
from ai_engine.domain.errors import FailureReason
from ai_engine.domain.job import ArtifactDescriptor


@runtime_checkable
class TokenProvider(Protocol):
    """Supplies the Backend bearer token (REQ-AUTH-01..04).

    Implementations MUST cache in process memory only -- REQ-AUTH-03 forbids a database --
    refresh when fewer than 30 s of the 300 s life remain, and make the refresh
    single-flight so N concurrent page tasks cause one token request rather than N.
    """

    async def token(self) -> str: ...

    async def invalidate(self) -> None:
        """Force the next :meth:`token` call to refresh. Called after a 401."""
        ...


@dataclass(frozen=True, slots=True)
class FetchedSource:
    local_path: str
    size_in_bytes: int
    content_type: str | None = None


@runtime_checkable
class SourceFetcher(Protocol):
    """Turns a presigned URL into a local file, surviving expiry (REQ-SRC-01/02).

    A 403 on a presigned GET means *expired*, not *forbidden*, so it triggers one refresh
    via `get-download-url` and one retry -- guarded against looping.
    """

    async def fetch(
        self, *, source_download_url: str, file_id: str, destination_dir: str
    ) -> FetchedSource: ...


@runtime_checkable
class ArtifactUploader(Protocol):
    """Executes the mandated three-step upload cycle for one file (REQ-UPL-01..03).

    `initialize-upload` -> presigned `PUT` -> `confirm-upload`, returning the Backend's
    file id. The caller records that id only after confirmation succeeds.
    """

    async def upload(
        self, *, descriptor: ArtifactDescriptor, folder_id: str, path_prefix: str
    ) -> str: ...


@runtime_checkable
class JobCallbacks(Protocol):
    """The four outbound progress calls.

    Production implementations write to a durable outbox rather than calling inline: if the
    process dies between the last `confirm-upload` and the PATCH, the page is otherwise
    lost forever and the book stalls.
    """

    async def report_page_count(self, job_id: UUID, pages_count: int) -> None: ...

    async def report_page_completed(
        self, job_id: UUID, page_number: int, artifacts: list[tuple[ArtifactType, str]]
    ) -> None: ...

    async def report_page_failed(
        self, job_id: UUID, page_number: int, reason: FailureReason
    ) -> None: ...

    async def report_job_failed(self, job_id: UUID, reason: FailureReason) -> None: ...
