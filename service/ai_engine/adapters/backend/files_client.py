"""The Backend's file APIs: the three-step upload cycle, and download-URL refresh.

The cycle is mandated verbatim by the V1 spec:

    POST /api/files/initialize-upload  ->  { fileId, upload: { url, requiredHeaders } }
    PUT  <upload.url>  with exactly those requiredHeaders and the file body
    POST /api/files/{fileId}/confirm-upload

Two details are easy to get wrong and both are load-bearing:

* ``requiredHeaders`` go onto the PUT **verbatim** -- not re-cased, not reordered, nothing
  added. A presigned signature covers them, so any mutation invalidates it.
* A ``403`` on a presigned URL almost always means *expired*, not *forbidden*. Treating it
  as terminal would fail uploads that merely need a fresh ticket.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ai_engine.adapters.http.retry import RetryExhausted, RetryPolicy, send_with_retry
from ai_engine.domain.errors import (
    ErrorCode,
    SourceUnavailableError,
    UploadFailedError,
)
from ai_engine.domain.job import ArtifactDescriptor
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.backend import TokenProvider

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UploadTicket:
    backend_file_id: str
    url: str
    required_headers: dict[str, str]


class BackendFilesClient:
    """Implements :class:`~ai_engine.ports.backend.ArtifactUploader` and source refresh."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient,
        tokens: TokenProvider,
        metrics: Metrics,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._tokens = tokens
        self._metrics = metrics
        self._policy = policy or RetryPolicy()

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.token()}"}

    # ── Source download URL refresh (REQ-SRC-02) ─────────────────────────────────

    async def get_download_url(self, file_id: str) -> str:
        """Ask the Backend for a fresh presigned URL for a file we already know."""
        path = f"/api/files/{file_id}/get-download-url"

        async def send() -> httpx.Response:
            return await self._client.get(
                f"{self._base_url}{path}", headers=await self._auth_headers()
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="backend.get_download_url",
                code=ErrorCode.SOURCE_UNAVAILABLE,
                on_unauthorized=self._tokens.invalidate,
            )
        except RetryExhausted as exc:
            raise SourceUnavailableError(
                f"could not refresh the download URL after {exc.attempts} attempts "
                f"({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            raise SourceUnavailableError(f"get-download-url returned HTTP {response.status_code}")

        url = response.json().get("url")
        if not url:
            raise SourceUnavailableError("get-download-url response had no url")
        # Never log the URL itself: the signature in it is a bearer credential.
        log.info("source.url_refreshed", file_id=file_id)
        return str(url)

    # ── The three-step upload cycle (REQ-UPL-01..03) ──────────────────────────────

    async def upload(
        self, *, descriptor: ArtifactDescriptor, folder_id: str, path_prefix: str
    ) -> str:
        ticket = await self._initialize_upload(
            descriptor=descriptor, folder_id=folder_id, path_prefix=path_prefix
        )
        try:
            await self._put_bytes(ticket, descriptor.payload)
        except UploadFailedError:
            # A presigned PUT that fails as expired needs a *fresh ticket*, not a retry of
            # the same one. Exactly one such recovery, so a systematic failure cannot loop.
            log.info(
                "upload.ticket_refresh",
                artifact_type=descriptor.artifact_type.value,
            )
            ticket = await self._initialize_upload(
                descriptor=descriptor, folder_id=folder_id, path_prefix=path_prefix
            )
            await self._put_bytes(ticket, descriptor.payload)

        await self._confirm_upload(ticket.backend_file_id)
        log.info(
            "artifact.uploaded",
            artifact_type=descriptor.artifact_type.value,
            size_in_bytes=descriptor.size_in_bytes,
            backend_file_id=ticket.backend_file_id,
        )
        return ticket.backend_file_id

    async def _initialize_upload(
        self, *, descriptor: ArtifactDescriptor, folder_id: str, path_prefix: str
    ) -> UploadTicket:
        body = {
            "fileName": descriptor.file_name,
            "contentType": descriptor.content_type,
            "sizeInBytes": descriptor.size_in_bytes,
            "folderId": folder_id,
            "pathPrefix": path_prefix,
        }

        async def send() -> httpx.Response:
            return await self._client.post(
                f"{self._base_url}/api/files/initialize-upload",
                json=body,
                headers=await self._auth_headers(),
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="backend.initialize_upload",
                code=ErrorCode.UPLOAD_FAILED,
                on_unauthorized=self._tokens.invalidate,
            )
        except RetryExhausted as exc:
            raise UploadFailedError(
                f"artifact {descriptor.artifact_type.value} failed at initialize-upload "
                f"after {exc.attempts} attempts ({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            raise UploadFailedError(
                f"artifact {descriptor.artifact_type.value} failed at initialize-upload "
                f"(HTTP {response.status_code})"
            )

        payload = response.json()
        upload = payload.get("upload") or {}
        file_id = payload.get("fileId")
        url = upload.get("url")
        if not file_id or not url:
            raise UploadFailedError("initialize-upload response was missing fileId or url")

        return UploadTicket(
            backend_file_id=str(file_id),
            url=str(url),
            required_headers=dict(upload.get("requiredHeaders") or {}),
        )

    async def _put_bytes(self, ticket: UploadTicket, payload: bytes) -> None:
        async def send() -> httpx.Response:
            # requiredHeaders verbatim. No Authorization header: the URL is presigned, and
            # attaching our Backend token here would hand it to the storage provider.
            return await self._client.put(
                ticket.url, content=payload, headers=ticket.required_headers
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="storage.put",
                code=ErrorCode.UPLOAD_FAILED,
            )
        except RetryExhausted as exc:
            raise UploadFailedError(
                f"presigned PUT failed after {exc.attempts} attempts ({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            raise UploadFailedError(f"presigned PUT returned HTTP {response.status_code}")

    async def _confirm_upload(self, file_id: str) -> None:
        async def send() -> httpx.Response:
            return await self._client.post(
                f"{self._base_url}/api/files/{file_id}/confirm-upload",
                headers=await self._auth_headers(),
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="backend.confirm_upload",
                code=ErrorCode.UPLOAD_FAILED,
                on_unauthorized=self._tokens.invalidate,
            )
        except RetryExhausted as exc:
            raise UploadFailedError(
                f"confirm-upload failed after {exc.attempts} attempts ({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            raise UploadFailedError(f"confirm-upload returned HTTP {response.status_code}")
