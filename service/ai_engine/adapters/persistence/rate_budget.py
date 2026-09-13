"""Provider budget counters: unlimited, per-process, and fleet-wide.

`T-5.7`. Three implementations of one port:

* :class:`UnlimitedBudget` -- nothing configured, nothing metered. The default, because a
  wrong low default would silently park real books.
* :class:`InMemoryBudget` -- one process. Correct for tests and a single-worker
  deployment; **not** a fleet-wide budget, and it says so if you ask.
* :class:`SqlBudget` -- the real one. A row per provider per window in the same database
  as the job store, incremented under ``BEGIN IMMEDIATE`` so that two workers racing for
  the last call of a 10-per-day free tier cannot both win.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_engine.adapters.persistence.sql import open_connection
from ai_engine.domain.budget import (
    FREE_PROVIDERS,
    BudgetUsage,
    ProviderLimit,
    decide,
)
from ai_engine.domain.errors import RateLimitedError
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import BUDGET_EXHAUSTED, Metrics

__all__ = ["InMemoryBudget", "SqlBudget", "UnlimitedBudget", "build_rate_budget"]

log = get_logger(__name__)

#: Windows older than this many periods are pruned when a provider is next charged.
_KEEP_WINDOWS = 3


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _refuse(limit: ProviderLimit, used: int, retry_after: float, metrics: Metrics | None) -> None:
    if metrics is not None:
        metrics.inc(BUDGET_EXHAUSTED, {"provider": limit.provider})
    log.warning(
        "provider_budget.exhausted",
        provider=limit.provider,
        used=used,
        max_calls=limit.max_calls,
        retry_after_seconds=round(retry_after, 1),
    )
    raise RateLimitedError(
        f"{limit.provider} budget exhausted: {used}/{limit.max_calls} calls "
        f"in this {limit.period_seconds}s window",
        retry_after_seconds=retry_after,
    )


class UnlimitedBudget:
    """No limits. Every call is allowed and nothing is counted."""

    fleet_wide = False

    async def acquire(self, provider: str, *, cost: int = 1) -> None:
        return None

    async def usage(self) -> list[BudgetUsage]:
        return []


class InMemoryBudget:
    """Per-process counters. Honest about not being fleet-wide."""

    fleet_wide = False

    def __init__(self, limits: dict[str, ProviderLimit], *, metrics: Metrics | None = None) -> None:
        self._limits = limits
        self._metrics = metrics
        self._used: dict[tuple[str, datetime], int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, provider: str, *, cost: int = 1) -> None:
        limit = self._limits.get(provider.lower())
        if limit is None or provider.lower() in FREE_PROVIDERS:
            return None
        now = _now()
        key = (limit.provider, limit.window_start(now))
        async with self._lock:
            used = self._used.get(key, 0)
            decision = decide(limit, used=used, cost=cost, now=now)
            if not decision.allowed:
                _refuse(limit, used, decision.retry_after_seconds, self._metrics)
            self._used[key] = used + cost

    async def usage(self) -> list[BudgetUsage]:
        now = _now()
        return [
            BudgetUsage(
                provider=limit.provider,
                used=self._used.get((limit.provider, limit.window_start(now)), 0),
                max_calls=limit.max_calls,
                window_start=limit.window_start(now),
                window_reset=limit.window_reset(now),
            )
            for limit in self._limits.values()
        ]


class SqlBudget:
    """The fleet-wide counter.

    Uses its own connection to the job store's database. Each ``acquire`` is one short
    ``BEGIN IMMEDIATE`` transaction: read the window's count, decide, write. SQLite
    serialises those across connections *and processes*, which is exactly the property a
    fleet-wide budget needs. In Postgres the same three statements become
    ``SELECT ... FOR UPDATE`` in a transaction -- see ``POSTGRES_DELTA`` in
    :mod:`ai_engine.adapters.persistence.sql`.
    """

    fleet_wide = True

    def __init__(
        self,
        path: str | Path,
        limits: dict[str, ProviderLimit],
        *,
        metrics: Metrics | None = None,
    ) -> None:
        self._path = str(path)
        self._limits = limits
        self._metrics = metrics
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        async with self._lock:
            if self._connection is None:
                self._connection = await asyncio.to_thread(open_connection, self._path)

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await asyncio.to_thread(self._connection.close)
                self._connection = None

    async def __aenter__(self) -> SqlBudget:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _run(self, work: Any, *args: Any) -> Any:
        async with self._lock:
            if self._connection is None:
                # Connect on first use. Unlike the job store there is no bootstrap step
                # worth failing at, and a budget that refuses to answer would either fail
                # jobs or -- worse -- be skipped.
                self._connection = await asyncio.to_thread(open_connection, self._path)
            return await asyncio.to_thread(work, self._connection, *args)

    # ── The port ──────────────────────────────────────────────────────────────────

    async def acquire(self, provider: str, *, cost: int = 1) -> None:
        limit = self._limits.get(provider.lower())
        if limit is None or provider.lower() in FREE_PROVIDERS:
            return None
        now = _now()

        def work(connection: sqlite3.Connection) -> int:
            """Returns the count *before* this charge, or -1 when refused."""
            window = limit.window_start(now).isoformat()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT used FROM provider_budget WHERE provider = ? AND window_start = ?",
                    (limit.provider, window),
                ).fetchone()
                used = int(row["used"]) if row is not None else 0
                if not decide(limit, used=used, cost=cost, now=now).allowed:
                    connection.execute("COMMIT")
                    return -1
                connection.execute(
                    """
                    INSERT INTO provider_budget (provider, window_start, used, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (provider, window_start)
                        DO UPDATE SET used = used + ?, updated_at = ?
                    """,
                    (
                        limit.provider,
                        window,
                        cost,
                        now.isoformat(),
                        cost,
                        now.isoformat(),
                    ),
                )
                # Opportunistic pruning: a daily window leaves one row per day per
                # provider, which is nothing, but there is no reason to keep it forever.
                cutoff = (
                    limit.window_start(now)
                    - timedelta(seconds=limit.period_seconds * _KEEP_WINDOWS)
                ).isoformat()
                connection.execute(
                    "DELETE FROM provider_budget WHERE provider = ? AND window_start < ?",
                    (limit.provider, cutoff),
                )
                connection.execute("COMMIT")
                return used
            except BaseException:
                connection.execute("ROLLBACK")
                raise

        used = await self._run(work)
        if used < 0:
            decision = decide(limit, used=limit.max_calls, cost=cost, now=now)
            _refuse(limit, limit.max_calls, decision.retry_after_seconds, self._metrics)

    async def usage(self) -> list[BudgetUsage]:
        now = _now()

        def work(connection: sqlite3.Connection) -> list[BudgetUsage]:
            out: list[BudgetUsage] = []
            for limit in self._limits.values():
                row = connection.execute(
                    "SELECT used FROM provider_budget WHERE provider = ? AND window_start = ?",
                    (limit.provider, limit.window_start(now).isoformat()),
                ).fetchone()
                out.append(
                    BudgetUsage(
                        provider=limit.provider,
                        used=int(row["used"]) if row is not None else 0,
                        max_calls=limit.max_calls,
                        window_start=limit.window_start(now),
                        window_reset=limit.window_reset(now),
                    )
                )
            return out

        return await self._run(work)


def build_rate_budget(
    *, settings: Any, metrics: Metrics | None = None
) -> UnlimitedBudget | InMemoryBudget | SqlBudget:
    """Pick a budget implementation from configuration.

    No limits configured -> :class:`UnlimitedBudget`. Limits plus a SQL job store ->
    :class:`SqlBudget` sharing that database. Limits with the memory store ->
    :class:`InMemoryBudget`, with a warning, because a per-process counter cannot hold a
    fleet-wide ceiling.
    """
    limits = settings.provider_limits()
    if not limits:
        return UnlimitedBudget()
    if getattr(settings, "job_store", "memory") == "memory":
        log.warning(
            "provider_budget.not_fleet_wide",
            reason="JOB_STORE=memory, so the budget is per-process only",
            providers=sorted(limits),
        )
        return InMemoryBudget(limits, metrics=metrics)
    return SqlBudget(settings.job_store_path, limits, metrics=metrics)
