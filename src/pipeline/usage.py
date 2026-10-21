"""Record every paid provider call to work/<book>/usage.jsonl (spec 025).

Drivers open a recording scope for the book and a page scope inside their loop; the
provider modules call record() right after each response comes back -- before parsing it,
because a response that then fails to parse was still billed. Outside a scope (library
use, tests) record() writes nothing, so the provider modules stay usable on their own.

One JSON line per call, the UsageRecord fields (usage_model.py). tools/cost_report.py
turns the file into calls, units and dollars using config/prices.toml.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from pipeline.usage_model import UsageRecord

_LOG: ContextVar[Path | None] = ContextVar("usage_log", default=None)
_STAGE: ContextVar[str | None] = ContextVar("usage_stage", default=None)
_PAGE: ContextVar[int | None] = ContextVar("usage_page", default=None)

LOG_NAME = "usage.jsonl"


@contextmanager
def recording(work_dir: Path, stage: str):
    """Send every record() inside this block to <work_dir>/usage.jsonl, tagged with stage."""
    log, st = _LOG.set(Path(work_dir) / LOG_NAME), _STAGE.set(stage)
    try:
        yield
    finally:
        _LOG.reset(log)
        _STAGE.reset(st)


@contextmanager
def page(number: int):
    token = _PAGE.set(number)
    try:
        yield
    finally:
        _PAGE.reset(token)


def record(provider: str, model: str, operation: str, **units) -> UsageRecord | None:
    """Append one call's usage if a recording scope is open. Returns the record written."""
    path = _LOG.get()
    if path is None:
        return None
    rec = UsageRecord(provider=provider, model=model, operation=operation,
                      stage=_STAGE.get(), page=_PAGE.get(), **units)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
    return rec


def gemini_units(response) -> dict:
    """Billed token counts from a google-genai response. Thinking tokens bill as output."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    return {
        "input_tokens": getattr(meta, "prompt_token_count", None) or 0,
        "output_tokens": (getattr(meta, "candidates_token_count", None) or 0)
                         + (getattr(meta, "thoughts_token_count", None) or 0),
    }


def openrouter_units(payload: dict) -> dict:
    """Token counts and the exact billed cost from an OpenRouter response body."""
    usage = payload.get("usage") or {}
    units = {"input_tokens": usage.get("prompt_tokens") or 0,
             "output_tokens": usage.get("completion_tokens") or 0}
    if usage.get("cost") is not None:
        units["reported_cost_usd"] = float(usage["cost"])
    return units


def read_log(work_dir: Path) -> list[UsageRecord]:
    path = Path(work_dir) / LOG_NAME
    if not path.exists():
        return []
    return [UsageRecord.from_dict(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
