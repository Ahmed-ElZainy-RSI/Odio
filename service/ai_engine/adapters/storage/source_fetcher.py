"""Fetch a job's source file from presigned storage.

Implements REQ-SRC-01/02 plus the security controls the spec does not mention but that a
server-side fetcher of caller-supplied URLs unavoidably needs.

The interesting behaviour is the 403 rule: for a presigned URL, ``403`` almost always means
*expired* rather than *forbidden*, so it triggers exactly one refresh via the Backend's
`get-download-url` and one retry. Exactly one, guarded, so a systematically bad URL cannot
become an infinite refresh loop.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ai_engine.adapters.backend.files_client import BackendFilesClient
from ai_engine.adapters.http.retry import RetryExhausted, RetryPolicy, send_with_retry
from ai_engine.domain.errors import (
    ErrorCode,
    SourceInvalidError,
    SourceUnavailableError,
    ValidationError,
)
from ai_engine.observability.logging import get_logger
from ai_engine.ports.backend import FetchedSource

log = get_logger(__name__)

#: A PDF must start with this. A Content-Type header is a claim; magic bytes are evidence.
PDF_MAGIC = b"%PDF-"

_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata.google.internal", "metadata", "instance-data"}
)


def _is_private_address(host: str) -> bool:
    """Resolve and reject loopback, link-local and private ranges.

    Link-local (169.254.0.0/16) is the important one: it is where cloud instance metadata
    services live, and reaching one from inside a worker is the classic SSRF payoff.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # unresolvable; the request will fail on its own
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_private
            or parsed.is_reserved
            or parsed.is_multicast
        ):
            return True
    return False


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """Everything the fetcher is allowed to do, in one value."""

    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = True
    max_bytes: int = 209_715_200
    resolve_and_check: bool = True


def validate_source_url(url: str, policy: SourcePolicy) -> None:
    """Apply the SSRF controls. Raises :class:`ValidationError` on rejection."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if policy.require_https and parsed.scheme != "https":
        raise ValidationError("sourceDownloadUrl must use https")
    if not host:
        raise ValidationError("sourceDownloadUrl must include a host")
    if host in _BLOCKED_HOSTNAMES:
        raise ValidationError("sourceDownloadUrl host is not permitted")

    if policy.allowed_hosts:
        allowed = [h.lower() for h in policy.allowed_hosts]
        if not any(host == entry or host.endswith(f".{entry}") for entry in allowed):
            raise ValidationError("sourceDownloadUrl host is not on the allowlist")

    if policy.resolve_and_check and _is_private_address(host):
        raise ValidationError("sourceDownloadUrl resolves to a non-public address")


class PresignedSourceFetcher:
    """Implements :class:`~ai_engine.ports.backend.SourceFetcher`."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        files: BackendFilesClient,
        policy: SourcePolicy,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._client = client
        self._files = files
        self._policy = policy
        self._retry = retry or RetryPolicy(max_attempts=3)

    async def fetch(
        self, *, source_download_url: str, file_id: str, destination_dir: str
    ) -> FetchedSource:
        validate_source_url(source_download_url, self._policy)

        destination = Path(destination_dir)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "source.bin"

        try:
            result = await self._download(source_download_url, target)
        except _PresignedExpired:
            log.info("source.expired", file_id=file_id)
            fresh_url = await self._files.get_download_url(file_id)
            validate_source_url(fresh_url, self._policy)
            try:
                result = await self._download(fresh_url, target)
            except _PresignedExpired as exc:
                # One refresh only. A second expiry means something else is wrong.
                raise SourceUnavailableError(
                    "download URL was still rejected after one refresh"
                ) from exc

        log.info("source.fetched", file_id=file_id, size_in_bytes=result.size_in_bytes)
        return result

    async def _download(self, url: str, target: Path) -> FetchedSource:
        async def send() -> httpx.Response:
            return await self._client.get(url)

        try:
            response = await send_with_retry(
                send,
                policy=self._retry,
                operation="storage.get",
                code=ErrorCode.SOURCE_UNAVAILABLE,
            )
        except RetryExhausted as exc:
            raise SourceUnavailableError(
                f"could not download the source after {exc.attempts} attempts ({exc.last_error})"
            ) from exc

        if response.status_code == 403:
            raise _PresignedExpired
        if response.status_code >= 400:
            raise SourceUnavailableError(f"source download returned HTTP {response.status_code}")

        payload = response.content
        if len(payload) > self._policy.max_bytes:
            raise SourceInvalidError(
                f"source is {len(payload)} bytes, exceeding the {self._policy.max_bytes}-byte limit"
            )
        if not payload:
            raise SourceInvalidError("source download was empty")

        target.write_bytes(payload)
        return FetchedSource(
            local_path=str(target),
            size_in_bytes=len(payload),
            content_type=response.headers.get("content-type"),
        )


class _PresignedExpired(Exception):
    """Internal signal: a presigned URL returned 403, i.e. it needs refreshing."""


def verify_pdf(path: str) -> None:
    """Reject anything that is not a parseable-looking PDF.

    A parse failure must become `SOURCE_INVALID` data rather than a worker crash: these
    bytes are untrusted input handed to native libraries.
    """
    head = Path(path).read_bytes()[:1024]
    if not head.startswith(PDF_MAGIC):
        raise SourceInvalidError(f"downloaded file is not a PDF (magic bytes {head[:8]!r})")


def verify_utf8_text(path: str) -> str:
    """Decode a Job B text source, or reject it."""
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceInvalidError("source text file is not valid UTF-8") from exc
