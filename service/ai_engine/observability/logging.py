"""Structured logging.

The existing code logs with bare ``print()`` -- roughly 20 calls in
``walidapro/ocr_verifier/final_context_reviewer.py`` alone -- with no level, no logger and
no correlation, so concurrent pages produce unattributable interleaved output.

Every record here is one JSON object with a fixed field set, and a redaction processor runs
over all of them. Redaction is structural rather than a matter of discipline at each call
site: a presigned URL is a bearer credential, and logging one is equivalent to logging a
password with a long expiry.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from ai_engine.domain.errors import redact
from ai_engine.observability.context import current_context

#: Keys whose values are always replaced wholesale, whatever they contain.
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "access_token",
        "authorization",
        "client_secret",
        "api_key",
        "apikey",
        "password",
        "secret",
        "source_download_url",
        "sourcedownloadurl",
        "upload_url",
        "presigned_url",
        "url",
    }
)


#: Keys carrying our own routes, event names or identifiers -- never credentials. Exempt
#: from free-text scrubbing, because redacting `/api/jobs` destroys the operator's ability
#: to see which endpoint failed, which is the whole point of the log line. Over-redaction
#: is a real cost, not a free safety margin.
_ROUTE_KEYS = frozenset({"path", "endpoint", "route", "event", "stage", "hint"})


def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop sensitive keys and scrub credential-shaped substrings from the rest."""
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in _SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
        elif lowered in _ROUTE_KEYS:
            continue
        elif isinstance(event_dict[key], str):
            event_dict[key] = redact(event_dict[key])
    return event_dict


def _context_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Merge the ambient correlation context into every record."""
    for key, value in current_context().items():
        event_dict.setdefault(key, value)
    return event_dict


def _rename_event(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Use `event` as the stable dotted event name, matching the documented schema."""
    if "event" in event_dict and "message" not in event_dict:
        return event_dict
    return event_dict


class _LateBoundStdoutLogger:
    """Writes to whatever ``sys.stdout`` is *at write time*.

    structlog's bundled factories capture the stream object when logging is configured.
    That looks harmless and is not: anything that later replaces ``sys.stdout`` -- pytest's
    capture fixtures, a supervisor reopening streams, a log-rotation wrapper -- leaves the
    logger holding a closed file, and every subsequent log call raises
    ``ValueError: I/O operation on closed file``. Resolving the stream per write costs
    nothing and removes the whole failure mode.
    """

    __slots__ = ()

    def msg(self, message: str) -> None:
        print(message, file=sys.stdout)

    log = debug = info = warn = warning = error = critical = exception = failure = msg


class _LateBoundLoggerFactory:
    __slots__ = ()

    def __call__(self, *args: Any) -> _LateBoundStdoutLogger:
        return _LateBoundStdoutLogger()


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = True,
    service: str = "ai-engine",
    role: str = "api",
    version: str = "0.1.0",
) -> None:
    """Install the logging configuration. Idempotent; safe to call per process start."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    # ConsoleRenderer formats exceptions itself, so `format_exc_info` must only be in the
    # chain for the JSON renderer -- structlog warns otherwise, and the warning is right.
    renderer: Any
    tail: list[Any]
    if json_logs:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
        tail = [structlog.processors.format_exc_info, renderer]
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
        tail = [renderer]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _context_processor,
            _rename_event,
            _redact_processor,
            *tail,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=_LateBoundLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service, role=role, version=version)


def get_logger(name: str = "ai_engine") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[return-value]
