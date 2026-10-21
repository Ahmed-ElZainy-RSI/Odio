"""Spec 029: the source fetcher refuses redirects, by decision rather than by accident.

The SSRF controls validate the URL we were *given*. A ``302`` pointing somewhere else --
the cloud metadata address, a private range -- would bypass all of them if it were
followed. httpx happens not to follow redirects by default; these tests pin the behaviour
even when the client is configured to follow them, and check that a 3xx is reported as a
refused redirect rather than saved as if it were the source.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ai_engine.adapters.storage.source_fetcher import PresignedSourceFetcher, SourcePolicy
from ai_engine.domain.errors import ErrorCode, SourceInvalidError

SOURCE = "https://storage.example.com/uploads/book.pdf?X-Amz-Signature=secret123"
PDF = b"%PDF-1.7\n" + b"x" * 64


class NoRefreshFiles:
    async def get_download_url(self, file_id: str) -> str:
        raise AssertionError("a redirect must not trigger a presigned-URL refresh")


def fetcher(handler, *, follow_redirects: bool = False) -> tuple[PresignedSourceFetcher, list]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(recording), follow_redirects=follow_redirects
    )
    policy = SourcePolicy(resolve_and_check=False)  # no DNS in unit tests
    return PresignedSourceFetcher(client=client, files=NoRefreshFiles(), policy=policy), seen


def redirect_to(location: str, status: int = 302):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "storage.example.com":
            return httpx.Response(status, headers={"location": location})
        return httpx.Response(200, content=PDF)  # what the attacker would serve

    return handler


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://10.0.0.1/internal.pdf",
        "https://storage.example.com/other.pdf",  # even same-host: refused, not followed
    ],
)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_refused_and_not_followed(
    tmp_path: Path, location: str, status: int
) -> None:
    source, seen = fetcher(redirect_to(location, status))

    with pytest.raises(SourceInvalidError, match="redirect") as info:
        await source.fetch(source_download_url=SOURCE, file_id="f1", destination_dir=str(tmp_path))

    assert len(seen) == 1, "the redirect target must never be requested"
    assert info.value.reason.code is ErrorCode.SOURCE_INVALID
    assert not (tmp_path / "source.bin").exists(), "a redirect body must not be saved"


async def test_refused_even_when_the_client_is_configured_to_follow(tmp_path: Path) -> None:
    """The decision lives in the fetcher, not in an httpx default or a client setting."""
    source, seen = fetcher(
        redirect_to("http://169.254.169.254/latest/meta-data/"), follow_redirects=True
    )
    with pytest.raises(SourceInvalidError, match="redirect"):
        await source.fetch(source_download_url=SOURCE, file_id="f1", destination_dir=str(tmp_path))
    assert [r.url.host for r in seen] == ["storage.example.com"]


async def test_the_reason_does_not_leak_the_location_or_the_signature(tmp_path: Path) -> None:
    source, _ = fetcher(redirect_to("http://169.254.169.254/latest/meta-data/"))
    with pytest.raises(SourceInvalidError) as info:
        await source.fetch(source_download_url=SOURCE, file_id="f1", destination_dir=str(tmp_path))
    text = str(info.value.reason)
    assert "169.254" not in text
    assert "secret123" not in text
    assert "HTTP 302" in text


async def test_a_plain_200_is_still_fetched(tmp_path: Path) -> None:
    source, seen = fetcher(lambda request: httpx.Response(200, content=PDF))
    result = await source.fetch(
        source_download_url=SOURCE, file_id="f1", destination_dir=str(tmp_path)
    )
    assert result.size_in_bytes == len(PDF)
    assert len(seen) == 1
