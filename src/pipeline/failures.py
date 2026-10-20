"""Per-page failure handling shared by every run_*.py driver (spec 008).

The unit of failure is the page. A driver catches each page's exception and
asks classify_failure() what it means:

  "quota" -- the provider refused for quota/rate/credit reasons. Stop the run:
             every further call would fail the same way and some providers
             count refused calls too. Nothing is recorded; the next run picks
             up from this page exactly as before.
  "bug"   -- a programming error (NameError/AttributeError/TypeError). Re-raise.
             A bug must never masquerade as "this page was hard" (spec 007;
             the same rule walidapro's functional audit enforces).
  "page"  -- anything else: a malformed model response, an unrenderable page,
             a transient network error. Record it and move on to the next page.

Failures are recorded in <work_dir>/failures.json, keyed by page and stage,
never in the page record's status. Changing status would break resume:
a TTS failure on a 'diacritised' page marked 'failed' would make
run_tashkel.py diacritise it again, and an OCR failure has no page record
to mark at all. Because status does not advance, the failed stage simply
retries the page next run, and a later success clears its entry.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

PROGRAMMING_ERRORS = (NameError, AttributeError, TypeError)

_QUOTA_MESSAGE = re.compile(
    r"RESOURCE_EXHAUSTED|\b429\b|quota|rate.?limit|too many requests|insufficient credit",
    re.IGNORECASE)


def _status_code(exc: BaseException):
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify_failure(exc: BaseException) -> str:
    """'quota' | 'bug' | 'page' -- see the module docstring."""
    if isinstance(exc, PROGRAMMING_ERRORS):
        return "bug"
    if _status_code(exc) == 429 or _QUOTA_MESSAGE.search(str(exc)):
        return "quota"
    return "page"


def _ledger(work_dir: Path) -> Path:
    return Path(work_dir) / "failures.json"


def _load(work_dir: Path) -> dict:
    path = _ledger(work_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(work_dir: Path, data: dict) -> None:
    path = _ledger(work_dir)
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def record(work_dir: Path, page: int, stage: str, exc: BaseException) -> None:
    data = _load(work_dir)
    data.setdefault(str(page), {})[stage] = {
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(work_dir, data)


def clear(work_dir: Path, page: int, stage: str) -> None:
    if not _ledger(work_dir).exists():
        return
    data = _load(work_dir)
    stages = data.get(str(page), {})
    if stages.pop(stage, None) is None:
        return
    if not stages:
        data.pop(str(page))
    _save(work_dir, data)


def handle(work_dir: Path, page: int, stage: str, exc: BaseException, label: str) -> str:
    """Classify, print, and record one page's exception. Returns the kind.

    Re-raises programming errors. The caller stops on "quota" and continues
    on "page".
    """
    kind = classify_failure(exc)
    if kind == "bug":
        raise exc
    print(f"{label}  FAILED: {type(exc).__name__}: {str(exc)[:150]}")
    if kind == "quota":
        print("     quota/rate limit -- stopping; rerun to resume from this page")
    else:
        record(work_dir, page, stage, exc)
        print(f"     recorded in {_ledger(work_dir).name}; continuing with the next page")
    return kind
