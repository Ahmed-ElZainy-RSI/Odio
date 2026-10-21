"""Collect paid-call usage where it happens, without threading a sink through every call.

The page task opens a collector around a stage; the adapters -- which run in worker
threads, where nothing can be awaited -- call :func:`record`. ``asyncio.to_thread`` and
``asyncio.run`` both copy the current context, so the collector set by the page task is the
one the adapter appends to. Outside a collector, :func:`record` does nothing, so adapters
stay usable on their own and in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ai_engine.domain.usage import UsageRecord

_COLLECTOR: ContextVar[list[UsageRecord] | None] = ContextVar("usage_collector", default=None)


@contextmanager
def collecting(*, page: int | None = None, stage: str | None = None) -> Iterator[list[UsageRecord]]:
    records: list[UsageRecord] = []
    token = _COLLECTOR.set(records)
    try:
        yield records
    finally:
        _COLLECTOR.reset(token)
        for i, r in enumerate(records):  # fill in what the adapter could not know
            if r.page is None or r.stage is None:
                records[i] = UsageRecord.from_dict(
                    r.to_dict() | {"page": r.page or page, "stage": r.stage or stage}
                )


def record(provider: str, model: str, operation: str, **units: Any) -> None:
    collector = _COLLECTOR.get()
    if collector is not None:
        collector.append(UsageRecord(provider=provider, model=model, operation=operation, **units))


def gemini_units(response: Any) -> dict[str, int]:
    """Billed token counts from a google-genai response; thinking tokens bill as output."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    return {
        "input_tokens": getattr(meta, "prompt_token_count", None) or 0,
        "output_tokens": (getattr(meta, "candidates_token_count", None) or 0)
        + (getattr(meta, "thoughts_token_count", None) or 0),
    }
