"""Correlation context.

`job_id` is the primary correlation key across the whole lifecycle, and it is deliberately
the Backend's OWN GUID. When they say "book X is stuck", that value is the only thing
anyone has to type -- no id mapping table, no shared tracing system.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_job_type: ContextVar[str | None] = ContextVar("job_type", default=None)
_page_number: ContextVar[int | None] = ContextVar("page_number", default=None)
_stage: ContextVar[str | None] = ContextVar("stage", default=None)


def current_context() -> dict[str, Any]:
    """The correlation fields set on the current task, omitting unset ones."""
    values = {
        "request_id": _request_id.get(),
        "job_id": _job_id.get(),
        "job_type": _job_type.get(),
        "page_number": _page_number.get(),
        "stage": _stage.get(),
    }
    return {key: value for key, value in values.items() if value is not None}


@contextmanager
def bind(
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    job_type: str | None = None,
    page_number: int | None = None,
    stage: str | None = None,
) -> Iterator[None]:
    """Bind correlation fields for the duration of a block, then restore."""
    tokens = []
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(request_id)))
    if job_id is not None:
        tokens.append((_job_id, _job_id.set(job_id)))
    if job_type is not None:
        tokens.append((_job_type, _job_type.set(job_type)))
    if page_number is not None:
        tokens.append((_page_number, _page_number.set(page_number)))
    if stage is not None:
        tokens.append((_stage, _stage.set(stage)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
