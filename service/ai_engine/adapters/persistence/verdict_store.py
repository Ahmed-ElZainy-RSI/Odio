"""Verdict stores (spec 022 T019): in-memory, and SQL in the job store's own database.

Chosen like the usage sink: a SQL job store gets :class:`SqlVerdictStore` on the same
database, the memory store gets :class:`InMemoryVerdictStore`. Volume is bounded by design:
at most ``VERIFIER_MAX_SUSPECTS`` verdicts per page plus the final review's suggestions.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ai_engine.adapters.persistence.sql import open_connection
from ai_engine.domain.evidence import Evidence


class InMemoryVerdictStore:
    def __init__(self) -> None:
        self._pages: dict[tuple[str, int], list[Evidence]] = {}

    async def replace_page(self, *, job_id: str, page: int, evidence: Sequence[Evidence]) -> None:
        self._pages[(job_id, page)] = list(evidence)

    async def for_job(self, job_id: str, *, page: int | None = None) -> list[tuple[int, Evidence]]:
        return [
            (p, e)
            for (j, p), rows in sorted(self._pages.items())
            if j == job_id and (page is None or p == page)
            for e in rows
        ]


class SqlVerdictStore:
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

    async def replace_page(self, *, job_id: str, page: int, evidence: Sequence[Evidence]) -> None:
        def work(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM page_verdicts WHERE job_id = ? AND page = ?", (job_id, page)
                )
                connection.executemany(
                    "INSERT INTO page_verdicts (job_id, page, token_id, status, applied, "
                    "evidence) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            job_id,
                            page,
                            e.token_id,
                            e.status,
                            int(e.applied),
                            json.dumps(e.to_dict(), ensure_ascii=False),
                        )
                        for e in evidence
                    ],
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

        await self._run(work)

    async def for_job(self, job_id: str, *, page: int | None = None) -> list[tuple[int, Evidence]]:
        def work(connection: sqlite3.Connection) -> list[tuple[int, Evidence]]:
            query = "SELECT page, evidence FROM page_verdicts WHERE job_id = ?"
            args: tuple[Any, ...] = (job_id,)
            if page is not None:
                query += " AND page = ?"
                args += (page,)
            rows = connection.execute(query + " ORDER BY page, id", args).fetchall()
            return [(row["page"], Evidence.from_dict(json.loads(row["evidence"]))) for row in rows]

        return await self._run(work)


def build_verdict_store(*, settings: Any) -> InMemoryVerdictStore | SqlVerdictStore:
    if getattr(settings, "job_store", "memory") == "memory":
        return InMemoryVerdictStore()
    return SqlVerdictStore(settings.job_store_path)
