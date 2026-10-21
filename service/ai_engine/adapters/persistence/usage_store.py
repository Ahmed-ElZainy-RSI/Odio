"""Usage sinks (spec 025): in-memory, and SQL in the job store's own database.

Chosen like the provider budget: a SQL job store gets :class:`SqlUsageSink` on the same
database (so cost is reported for the whole fleet), the memory store gets
:class:`InMemoryUsageSink`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ai_engine.adapters.persistence.sql import open_connection
from ai_engine.domain.usage import UsageRecord

_COLUMNS = (
    "provider",
    "model",
    "operation",
    "at",
    "page",
    "input_tokens",
    "output_tokens",
    "chars",
    "audio_ms",
    "pages",
    "images",
    "reported_cost_usd",
    "cached",
)


class InMemoryUsageSink:
    def __init__(self) -> None:
        self._rows: list[tuple[str | None, str | None, UsageRecord]] = []

    async def write(
        self, records: Sequence[UsageRecord], *, job_id: str | None, tier: str | None
    ) -> None:
        self._rows.extend((job_id, tier, r) for r in records)

    async def records(self, *, job_id: str | None = None) -> list[UsageRecord]:
        return [r for j, _t, r in self._rows if job_id is None or j == job_id]


class SqlUsageSink:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    async def _run(self, work: Any, *args: Any) -> Any:
        async with self._lock:
            if self._connection is None:
                self._connection = await asyncio.to_thread(open_connection, self._path)
            return await asyncio.to_thread(work, self._connection, *args)

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await asyncio.to_thread(self._connection.close)
                self._connection = None

    async def write(
        self, records: Sequence[UsageRecord], *, job_id: str | None, tier: str | None
    ) -> None:
        if not records:
            return

        def work(connection: sqlite3.Connection) -> None:
            placeholders = ", ".join("?" * (len(_COLUMNS) + 2))
            connection.executemany(
                f"INSERT INTO provider_usage (job_id, tier, {', '.join(_COLUMNS)}) "
                f"VALUES ({placeholders})",
                [
                    (
                        job_id,
                        tier,
                        *(int(getattr(r, c)) if c == "cached" else getattr(r, c) for c in _COLUMNS),
                    )
                    for r in records
                ],
            )

        await self._run(work)

    async def records(self, *, job_id: str | None = None) -> list[UsageRecord]:
        def work(connection: sqlite3.Connection) -> list[UsageRecord]:
            query = f"SELECT {', '.join(_COLUMNS)} FROM provider_usage"
            rows = (
                connection.execute(query + " WHERE job_id = ? ORDER BY id", (job_id,))
                if job_id is not None
                else connection.execute(query + " ORDER BY id")
            ).fetchall()
            return [
                UsageRecord.from_dict(
                    {c: (bool(row[c]) if c == "cached" else row[c]) for c in _COLUMNS}
                )
                for row in rows
            ]

        return await self._run(work)


def build_usage_sink(*, settings: Any) -> InMemoryUsageSink | SqlUsageSink:
    if getattr(settings, "job_store", "memory") == "memory":
        return InMemoryUsageSink()
    return SqlUsageSink(settings.job_store_path)
