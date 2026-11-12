"""Wall-clock timing for every pipeline step, written to work/<book>/timings.jsonl.

Same shape as usage.py: main.py opens a run scope (one run id per invocation), each driver
opens a stage scope and a page scope inside its loop, and the work itself is wrapped in
step("ocr") / step("tts") / ... . Every step appends one JSON line:

    {"run": "20261112T101500", "stage": "ocr-gemini", "page": 3, "step": "ocr",
     "seconds": 4.213, "ok": true, "at": "2026-11-12T10:15:07"}

A failed step is logged too (ok=false), because the time was still spent. Outside a run
scope (library use, tests) nothing is written, but step() still measures, so drivers can
print the time either way.

Times are wall clock and include waits: a Gemini step's seconds include any time spent
blocked in ratelimit.acquire() and any retries inside ocr_page()/synthesize().

summary() prints this run's per-page table and per-step totals; resumed pages that were
skipped this run have no rows, so the table shows only work actually done now.
"""
import json
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

_LOG: ContextVar[Path | None] = ContextVar("timing_log", default=None)
_RUN: ContextVar[str | None] = ContextVar("timing_run", default=None)
_STAGE: ContextVar[str | None] = ContextVar("timing_stage", default=None)
_PAGE: ContextVar[int | None] = ContextVar("timing_page", default=None)

LOG_NAME = "timings.jsonl"


class Timer:
    """What step() yields; .seconds is filled in when the block exits."""
    seconds: float = 0.0


@contextmanager
def run(work_dir: Path, run_id: str | None = None):
    """Send every step inside this block to <work_dir>/timings.jsonl under one run id."""
    run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    log, r = _LOG.set(Path(work_dir) / LOG_NAME), _RUN.set(run_id)
    try:
        yield run_id
    finally:
        _LOG.reset(log)
        _RUN.reset(r)


@contextmanager
def stage(name: str):
    token = _STAGE.set(name)
    try:
        yield
    finally:
        _STAGE.reset(token)


@contextmanager
def page(number: int):
    token = _PAGE.set(number)
    try:
        yield
    finally:
        _PAGE.reset(token)


@contextmanager
def step(name: str, log: bool = True):
    """Time the block; log it if a run scope is open (and log=True). Yields a Timer.

    log=False is for a block that wraps other logged steps (a whole page), so its
    time is printed but not counted twice in summary()."""
    t = Timer()
    start = time.perf_counter()
    ok = False
    try:
        yield t
        ok = True
    finally:
        t.seconds = time.perf_counter() - start
        if log:
            _write(name, t.seconds, ok)


def _write(name: str, seconds: float, ok: bool):
    path = _LOG.get()
    if path is None:
        return
    row = {"run": _RUN.get(), "stage": _STAGE.get(), "page": _PAGE.get(), "step": name,
           "seconds": round(seconds, 3), "ok": ok,
           "at": datetime.now().isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def fmt(seconds: float) -> str:
    """4.2s / 3m07s / 1h02m."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def load(work_dir: Path, run_id: str | None = None) -> list[dict]:
    path = Path(work_dir) / LOG_NAME
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if run_id is None or r.get("run") == run_id]


def summary(work_dir: Path, run_id: str, total_seconds: float | None = None) -> str:
    """Text report of one run: a per-page table (one column per step) and per-step totals."""
    rows = [r for r in load(work_dir, run_id) if r["step"] != BOOK_TOTAL_STEP]
    lines = [f"--- timing (run {run_id}) ---"]
    if not rows:
        lines.append("no timed steps this run (everything was already done)")
    else:
        steps = list(dict.fromkeys(r["step"] for r in rows if r["page"] is not None))
        per_page: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in rows:
            if r["page"] is not None:
                per_page[r["page"]][r["step"]] += r["seconds"]
        if per_page:
            lines.append(f"{'pg':>4} " + " ".join(f"{s:>10}" for s in steps) + f" {'page total':>11}")
            lines.append("-" * (5 + 11 * len(steps) + 12))
            for pg in sorted(per_page):
                cells = per_page[pg]
                lines.append(f"{pg:>4} " + " ".join(
                    f"{fmt(cells[s]) if s in cells else '-':>10}" for s in steps)
                    + f" {fmt(sum(cells.values())):>11}")
            n = len(per_page)
            all_page = sum(sum(c.values()) for c in per_page.values())
            lines.append(f"\n{n} pages timed, {fmt(all_page)} of page work, {fmt(all_page / n)} per page on average")

        totals: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r in rows:
            totals[(r["stage"] or "-", r["step"])].append(r["seconds"])
        lines.append(f"\n{'stage':<16} {'step':<20} {'calls':>5} {'total':>8} {'avg':>7} {'max':>7}")
        for (st, sp), secs in totals.items():
            lines.append(f"{st:<16} {sp:<20} {len(secs):>5} {fmt(sum(secs)):>8} "
                         f"{fmt(sum(secs) / len(secs)):>7} {fmt(max(secs)):>7}")
        failed = sum(1 for r in rows if not r["ok"])
        if failed:
            lines.append(f"({failed} timed step(s) failed; their time is included)")
    if total_seconds is not None:
        lines.append(f"\nbook total (wall clock): {fmt(total_seconds)}")
    lines.append(f"log: {Path(work_dir) / LOG_NAME}")
    return "\n".join(lines)


BOOK_TOTAL_STEP = "book (total)"


def record_book_total(seconds: float):
    """Log the whole run's wall clock (page=None) so the reader can show it."""
    _write(BOOK_TOTAL_STEP, seconds, True)


def latest_by_page(work_dir: Path) -> dict[int, dict[str, float]]:
    """{page: {step: seconds}} from the most recent run that timed each page's step.

    Across runs, not just the last one: a resumed run skips finished pages, and those
    should keep the times they were made with. Within one run a step's rows are summed
    (a page diacritised twice in one run is unusual, but its time was spent)."""
    out: dict[int, dict[str, float]] = defaultdict(dict)
    newest_run: dict[tuple[int, str], str] = {}
    for r in load(work_dir):
        if r["page"] is None or not r.get("ok"):
            continue
        key = (r["page"], r["step"])
        if newest_run.get(key) != r["run"]:
            newest_run[key] = r["run"]  # rows are appended in time order: a new run resets
            out[r["page"]][r["step"]] = 0.0
        out[r["page"]][r["step"]] += r["seconds"]
    return {p: dict(s) for p, s in out.items()}


def latest_book_total(work_dir: Path) -> float | None:
    """Wall clock of the most recent full main.py run, or None if none was recorded."""
    rows = [r for r in load(work_dir) if r["step"] == BOOK_TOTAL_STEP]
    return rows[-1]["seconds"] if rows else None
