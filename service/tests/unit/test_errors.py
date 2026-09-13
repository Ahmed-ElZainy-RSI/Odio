"""Error taxonomy and redaction."""

from __future__ import annotations

import pytest

from ai_engine.domain.errors import (
    MAX_REASON_LENGTH,
    ErrorCode,
    FailureReason,
    SourceInvalidError,
    redact,
)


def test_every_error_code_has_http_mapping_and_message() -> None:
    for code in ErrorCode:
        assert 400 <= code.http_status <= 504
        assert code.safe_message
        assert isinstance(code.retryable, bool)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.VALIDATION_ERROR, False),
        (ErrorCode.CONFLICT, False),
        (ErrorCode.SOURCE_INVALID, False),
        (ErrorCode.AUTHORIZATION_ERROR, False),
        (ErrorCode.RATE_LIMITED, True),
        (ErrorCode.AI_INFERENCE_ERROR, True),
        (ErrorCode.UPLOAD_FAILED, True),
        (ErrorCode.CALLBACK_FAILED, True),
    ],
)
def test_retryable_classification(code: ErrorCode, expected: bool) -> None:
    assert code.retryable is expected


@pytest.mark.parametrize(
    "secret_bearing",
    [
        "download failed for https://s3.example.com/x?X-Amz-Signature=deadbeefcafe",
        "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.signature",
        "used api_key=sk-live-1234567890",
        "client_secret: super-secret-value",
        "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef",
        "crashed while reading /Users/someone/secrets/creds.json",
    ],
)
def test_reason_never_leaks_credentials_or_paths(secret_bearing: str) -> None:
    wire = FailureReason(ErrorCode.SOURCE_UNAVAILABLE, secret_bearing).to_wire()
    for marker in ("X-Amz-Signature", "eyJ", "sk-live", "super-secret", "/Users/"):
        assert marker not in wire, f"{marker!r} survived redaction in {wire!r}"
    assert "[REDACTED]" in wire


def test_reason_is_bounded() -> None:
    reason = FailureReason(ErrorCode.AI_INFERENCE_ERROR, "x" * 5000)
    wire = reason.to_wire()
    assert len(wire) <= MAX_REASON_LENGTH
    assert wire.startswith("AI_INFERENCE_ERROR: ")


def test_reason_wire_format_is_code_prefixed() -> None:
    wire = FailureReason(ErrorCode.PROCESSING_TIMEOUT, "reached stage 'align'").to_wire()
    assert wire == "PROCESSING_TIMEOUT: reached stage 'align'"


def test_redaction_leaves_ordinary_text_alone() -> None:
    text = "OCR provider returned HTTP 500 after 3 attempts"
    assert redact(text) == text


def test_exception_carries_code_and_retryability() -> None:
    exc = SourceInvalidError("not a PDF (magic bytes 'PK')")
    assert exc.reason.code is ErrorCode.SOURCE_INVALID
    assert exc.retryable is False
    assert "SOURCE_INVALID" in str(exc)


# ── Redaction must be narrow enough to stay useful ──────────────────────────────


@pytest.mark.parametrize(
    "route",
    ["/api/jobs", "/api/jobs/3f2504e0-4f89-11d3-9a0c-0305e82c3301", "/health", "/ready"],
)
def test_our_own_url_routes_survive_redaction(route: str) -> None:
    """Redacting `/api/jobs` would destroy the operator's ability to see what failed.

    Over-redaction is a real cost, not a free safety margin: a log line that cannot tell
    you which endpoint broke is close to worthless.
    """
    assert redact(f"request to {route} failed") == f"request to {route} failed"


@pytest.mark.parametrize(
    "filesystem_path",
    [
        "/Users/someone/secrets/creds.json",
        "/var/tmp/ai-engine/scratch/source.pdf",
        "/home/deploy/.env",
        "/etc/passwd",
    ],
)
def test_filesystem_paths_are_still_redacted(filesystem_path: str) -> None:
    scrubbed = redact(f"failed reading {filesystem_path}")
    assert filesystem_path not in scrubbed
    assert "[REDACTED]" in scrubbed
