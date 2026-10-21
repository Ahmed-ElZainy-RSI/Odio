"""A paid-call limiter shared by every process on the machine (spec 015).

Free tiers are per project per model (gemini-3.1-flash-tts: 10 requests/day,
gemini-3.6-flash: 20/day). One process at a time stayed under them by accident of being
slow; four parallel workers (batch.py) would just hit the wall four times faster and burn
the day's allowance on calls that fail.

Every paid call now asks `acquire(provider, model)` first. Counters live in one SQLite file
and are incremented inside `BEGIN IMMEDIATE`, so two processes racing for the last call of
a window cannot both win -- the same technique as the service's fleet-wide provider budget
(service/ai_engine/adapters/persistence/rate_budget.py). Windows are fixed and
epoch-aligned, so a daily limit resets at 00:00 UTC.

Configuration, opt-in (nothing set = nothing limited, exactly as before):

    PIPELINE_RATE_LIMITS="gemini:gemini-3.1-flash-tts-preview=10/86400,gemini:gemini-3.6-flash=20/86400"
    PIPELINE_RATE_DB=work/.ratelimit.sqlite     (default)

A key is `provider` or `provider:model`; the most specific matching key applies. An
exhausted window raises QuotaPaused, whose message the per-page failure handling (spec 008)
already classifies as quota: the driver stops that provider's work and the next run resumes.
"""
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path


class QuotaPaused(RuntimeError):
    """A local rate limit is exhausted. The message says 'rate limit' so failures.py
    classifies it as quota (stop, don't record a page failure)."""

    def __init__(self, key: str, used: int, limit: int, retry_after: float) -> None:
        super().__init__(f"local rate limit reached for {key}: {used}/{limit} in this window, "
                         f"resets in {retry_after:.0f}s")
        self.key, self.retry_after = key, retry_after


def parse_limits(spec: str) -> dict[str, tuple[int, int]]:
    """'gemini:m=10/86400, lighton=100/3600' -> {key: (max_calls, period_seconds)}."""
    limits = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        key, _, rate = part.partition("=")
        calls, _, period = rate.partition("/")
        if not key or not calls.isdigit() or not period.isdigit() or int(period) <= 0:
            raise ValueError(f"bad rate limit {part!r}: expected key=calls/seconds")
        limits[key.strip().lower()] = (int(calls), int(period))
    return limits


class RateLimiter:
    def __init__(self, db_path: Path, limits: dict[str, tuple[int, int]],
                 clock: Callable[[], float] = time.time) -> None:
        self._db = Path(db_path)
        self._limits = limits
        self._clock = clock

    def _limit_for(self, provider: str, model: str) -> tuple[str, int, int] | None:
        for key in (f"{provider}:{model}".lower(), provider.lower()):
            if key in self._limits:
                return (key, *self._limits[key])
        return None

    def acquire(self, provider: str, model: str = "") -> None:
        found = self._limit_for(provider, model)
        if found is None:
            return
        key, max_calls, period = found
        now = self._clock()
        window = int(now // period) * period
        self._db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db, timeout=30, isolation_level=None)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT, window INTEGER, used INTEGER,"
                        " PRIMARY KEY (key, window))")
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT used FROM calls WHERE key = ? AND window = ?",
                              (key, window)).fetchone()
            used = row[0] if row else 0
            if used >= max_calls:
                con.execute("ROLLBACK")
                raise QuotaPaused(key, used, max_calls, window + period - now)
            con.execute("INSERT INTO calls (key, window, used) VALUES (?, ?, 1) "
                        "ON CONFLICT (key, window) DO UPDATE SET used = used + 1", (key, window))
            con.execute("DELETE FROM calls WHERE key = ? AND window < ?", (key, window - 3 * period))
            con.execute("COMMIT")
        finally:
            con.close()


_LIMITER: RateLimiter | None = None
_CONFIGURED_FROM: tuple[str, str] | None = None


def acquire(provider: str, model: str = "") -> None:
    """Called before every paid request. A no-op unless PIPELINE_RATE_LIMITS is set."""
    global _LIMITER, _CONFIGURED_FROM
    spec = os.environ.get("PIPELINE_RATE_LIMITS", "")
    db = os.environ.get("PIPELINE_RATE_DB", "work/.ratelimit.sqlite")
    if not spec:
        return
    if _CONFIGURED_FROM != (spec, db):
        _LIMITER, _CONFIGURED_FROM = RateLimiter(Path(db), parse_limits(spec)), (spec, db)
    _LIMITER.acquire(provider, model)
