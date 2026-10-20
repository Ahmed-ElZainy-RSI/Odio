"""The unified error model.

One taxonomy used identically in API responses, log records, metric labels and the
`reason` strings sent to the Backend. See docs/backend-v1/14-error-model.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

#: Hard cap on a `reason` string sent to the Backend (docs 14-error-model.md section 3).
MAX_REASON_LENGTH = 512

_REDACTED = "[REDACTED]"

# A presigned URL is a bearer credential: the signature in the query string grants access
# to the object. Never let one reach a log line or the Backend's `reason` field.
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}")
_SECRETISH_RE = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|access[_-]?token|password|secret)"
    r"\s*[=:]\s*\S+"
)
# Absolute filesystem paths leak our internals into the Backend's UI. Deliberately
# narrower than "any slash-separated string": it requires either a recognisable filesystem
# root or a file extension, so URL routes like `/api/jobs` are NOT treated as paths.
_PATH_RE = re.compile(
    r"(?:/(?:Users|home|var|etc|tmp|opt|root|private|mnt|srv)(?:/[\w.\-]+)+)"
    r"|(?:/[\w.\-]+){2,}/[\w.\-]+\.[A-Za-z0-9]{1,6}\b"
)


def redact(text: str) -> str:
    """Strip credentials, URLs and filesystem paths out of free text.

    Applied at construction time in :class:`FailureReason` so that no code path can build
    an unsafe reason and later ship it.
    """
    if not text:
        return text
    out = _SECRETISH_RE.sub(_REDACTED, text)
    out = _JWT_RE.sub(_REDACTED, out)
    out = _BEARER_RE.sub(_REDACTED, out)
    out = _URL_RE.sub(_REDACTED, out)
    out = _PATH_RE.sub(_REDACTED, out)
    return out


#: Exceptions that mean *our* code is wrong, not that a provider, a file or the network
#: failed. Adapters that wrap provider failures into typed errors re-raise these untouched,
#: so a bug surfaces as INTERNAL_ERROR with a stack trace instead of masquerading as an
#: outage (spec 007; the same set walidapro's functional audit enforces). KeyError is
#: deliberately absent: a malformed provider payload raises it, and that is provider data.
PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (NameError, AttributeError, TypeError)


class ErrorCode(StrEnum):
    """The complete taxonomy. Every failure in the system maps to exactly one of these."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_INVALID = "SOURCE_INVALID"
    AI_INFERENCE_ERROR = "AI_INFERENCE_ERROR"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    CALLBACK_FAILED = "CALLBACK_FAILED"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self]

    @property
    def retryable(self) -> bool:
        """Whether *we* should retry the operation that produced this code."""
        return self in _RETRYABLE

    @property
    def safe_message(self) -> str:
        """A message that is always safe to return to a caller and to log."""
        return _SAFE_MESSAGE[self]


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTHENTICATION_ERROR: 401,
    ErrorCode.AUTHORIZATION_ERROR: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    # The codes below never originate from an inbound request; they surface as page or job
    # callbacks. 500/503 is what we would return if one somehow escaped into a response.
    ErrorCode.SOURCE_UNAVAILABLE: 503,
    ErrorCode.SOURCE_INVALID: 422,
    ErrorCode.AI_INFERENCE_ERROR: 500,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.PROCESSING_TIMEOUT: 504,
    ErrorCode.UPLOAD_FAILED: 502,
    ErrorCode.CALLBACK_FAILED: 502,
    ErrorCode.EXTERNAL_SERVICE_ERROR: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}

_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.SOURCE_UNAVAILABLE,
        ErrorCode.AI_INFERENCE_ERROR,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.PROCESSING_TIMEOUT,
        ErrorCode.UPLOAD_FAILED,
        ErrorCode.CALLBACK_FAILED,
        ErrorCode.EXTERNAL_SERVICE_ERROR,
        ErrorCode.INTERNAL_ERROR,
    }
)

_SAFE_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Request body failed validation",
    ErrorCode.AUTHENTICATION_ERROR: "Authentication failed",
    ErrorCode.AUTHORIZATION_ERROR: "Not authorized for this operation",
    ErrorCode.NOT_FOUND: "Resource not found",
    ErrorCode.CONFLICT: "jobId already exists with a different payload",
    ErrorCode.RATE_LIMITED: "Upstream rate limit reached",
    ErrorCode.SOURCE_UNAVAILABLE: "Could not download the source file",
    ErrorCode.SOURCE_INVALID: "Source file is not valid for this job type",
    ErrorCode.AI_INFERENCE_ERROR: "AI processing failed for this page",
    ErrorCode.MODEL_UNAVAILABLE: "AI model is temporarily unavailable",
    ErrorCode.PROCESSING_TIMEOUT: "Page processing exceeded the time limit",
    ErrorCode.UPLOAD_FAILED: "Could not upload the generated artifact",
    ErrorCode.CALLBACK_FAILED: "Could not deliver a progress callback",
    ErrorCode.EXTERNAL_SERVICE_ERROR: "A dependency is unavailable",
    ErrorCode.INTERNAL_ERROR: "An unexpected error occurred",
}


@dataclass(frozen=True, slots=True)
class FailureReason:
    """A bounded, redacted failure description safe to send to the Backend.

    Redaction and truncation happen in ``__post_init__``, so an unsafe reason cannot be
    constructed at all -- there is no code path that can build one and ship it later.
    """

    code: ErrorCode
    detail: str = ""

    def __post_init__(self) -> None:
        cleaned = redact(self.detail).strip()
        prefix = f"{self.code.value}: "
        budget = MAX_REASON_LENGTH - len(prefix)
        if len(cleaned) > budget:
            cleaned = cleaned[: budget - 1].rstrip() + "…"
        object.__setattr__(self, "detail", cleaned)

    @property
    def retryable(self) -> bool:
        return self.code.retryable

    def to_wire(self) -> str:
        """The exact string placed in a `{"reason": ...}` callback body.

        Format is ``<ERROR_CODE>: <human explanation>`` -- a stable machine-readable
        prefix plus a tail that can be reworded without breaking a parser.
        """
        text = f"{self.code.value}: {self.detail}" if self.detail else f"{self.code.value}"
        assert len(text) <= MAX_REASON_LENGTH
        return text


class AiEngineError(Exception):
    """Base class for every domain error. Carries an :class:`ErrorCode`."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(self, detail: str = "", *, code: ErrorCode | None = None) -> None:
        self.reason = FailureReason(code or self.code, detail)
        super().__init__(self.reason.to_wire())

    @property
    def retryable(self) -> bool:
        return self.reason.retryable


class ValidationError(AiEngineError):
    code = ErrorCode.VALIDATION_ERROR


class AuthenticationError(AiEngineError):
    code = ErrorCode.AUTHENTICATION_ERROR


class AuthorizationError(AiEngineError):
    code = ErrorCode.AUTHORIZATION_ERROR


class NotFoundError(AiEngineError):
    code = ErrorCode.NOT_FOUND


class ConflictError(AiEngineError):
    code = ErrorCode.CONFLICT


class SourceUnavailableError(AiEngineError):
    code = ErrorCode.SOURCE_UNAVAILABLE


class SourceInvalidError(AiEngineError):
    code = ErrorCode.SOURCE_INVALID


class AiInferenceError(AiEngineError):
    code = ErrorCode.AI_INFERENCE_ERROR


class ModelUnavailableError(AiEngineError):
    code = ErrorCode.MODEL_UNAVAILABLE


class RateLimitedError(AiEngineError):
    """A provider allowance is exhausted.

    Carries ``retry_after_seconds`` so callers can park work until the window resets
    rather than retrying into a wall. Zero means "unknown, use your own backoff".
    """

    code = ErrorCode.RATE_LIMITED

    def __init__(
        self,
        detail: str = "",
        *,
        code: ErrorCode | None = None,
        retry_after_seconds: float = 0.0,
    ) -> None:
        super().__init__(detail, code=code)
        self.retry_after_seconds = max(0.0, retry_after_seconds)


class ProcessingTimeoutError(AiEngineError):
    code = ErrorCode.PROCESSING_TIMEOUT


class UploadFailedError(AiEngineError):
    code = ErrorCode.UPLOAD_FAILED


class CallbackFailedError(AiEngineError):
    code = ErrorCode.CALLBACK_FAILED


class ExternalServiceError(AiEngineError):
    code = ErrorCode.EXTERNAL_SERVICE_ERROR
