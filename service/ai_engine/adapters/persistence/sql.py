"""A durable JobStore on stdlib ``sqlite3``.

Why SQLite and not Postgres: no Postgres server or Docker daemon is available in this
environment, and an untested SQL layer would be worse than none. SQLite 3.52 supports every
feature this store needs -- WAL, ``INSERT ... ON CONFLICT DO NOTHING RETURNING``, partial
indexes, CHECK constraints -- so the schema and the queries are written in the shape
Postgres wants, and :data:`POSTGRES_DELTA` documents exactly what changes.

What this buys, concretely: **the service becomes restart-safe.** A worker that dies no
longer takes its in-flight jobs with it, because the ledger, the artifact records and the
callback outbox all survive the process. That was the last thing standing between this
package and a real integration test.

What it does not buy: multi-host concurrency. SQLite serializes writers, so this is a
single-host store. That limit is deliberate and documented rather than hidden -- see
:ref:`concurrency` below.

Concurrency model
-----------------
``sqlite3`` is blocking, so every call runs in a worker thread. One connection is shared
and guarded by an ``asyncio.Lock``, which serializes our own access; SQLite serializes
writers anyway, so this is not a throughput loss. ``BEGIN IMMEDIATE`` takes the write lock
up front, which is what makes the claim queries atomic -- the single-writer equivalent of
``SELECT ... FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from ai_engine.domain.callbacks import CallbackEnvelope, OutboxRecord
from ai_engine.domain.enums import (
    ArtifactType,
    CallbackKind,
    JobState,
    JobType,
    PageState,
    QualityTier,
)
from ai_engine.domain.errors import ErrorCode, FailureReason
from ai_engine.domain.job import Artifact, Job, PageTask
from ai_engine.observability.logging import get_logger
from ai_engine.ports.job_store import AcceptOutcome, TerminalPageError

log = get_logger(__name__)

#: What changes when this becomes Postgres (T-4.1 proper). Kept in code so it cannot drift
#: away from the schema it describes.
POSTGRES_DELTA = """
1. Types: TEXT -> uuid / timestamptz / the ENUM types; INTEGER 0/1 -> boolean;
   payload TEXT -> jsonb.
2. Claims: replace `BEGIN IMMEDIATE` + SELECT/UPDATE with a single
   `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`.
   That is the only change that matters, and it makes the store multi-writer.
3. Time: `now()` server-side instead of Python-supplied ISO strings.
4. Everything else -- table shapes, PRIMARY KEYs, UNIQUE constraints, CHECK constraints,
   partial indexes, ON CONFLICT DO NOTHING -- transfers unchanged.
"""

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

-- provider_budget: the fleet-wide paid-call counter (T-5.7). One row per provider per
-- fixed window; `used` is incremented under BEGIN IMMEDIATE so two workers cannot both
-- be granted the last call of a free tier. Old windows are pruned opportunistically.
CREATE TABLE IF NOT EXISTS provider_budget (
    provider     TEXT NOT NULL,
    window_start TEXT NOT NULL,
    used         INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (provider, window_start)
);

-- jobs: the idempotency store. `job_id` PRIMARY KEY *is* the idempotency mechanism.
CREATE TABLE IF NOT EXISTS jobs (
    job_id            TEXT PRIMARY KEY,
    job_type          TEXT NOT NULL,
    state             TEXT NOT NULL DEFAULT 'QUEUED',
    file_id           TEXT NOT NULL,
    folder_id         TEXT NOT NULL,
    path_prefix       TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    page_number       INTEGER,
    quality_tier      TEXT NOT NULL DEFAULT 'fast',
    pages_count       INTEGER,
    metadata_sent_at  TEXT,
    payload_hash      TEXT NOT NULL,
    attempt           INTEGER NOT NULL DEFAULT 0,
    claimed_by        TEXT,
    lease_until       TEXT,
    failure_code      TEXT,
    failure_reason    TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    CONSTRAINT job_b_has_page CHECK (
        (job_type = 'PAGE_AUDIO_AND_TEXT_TIMING_GENERATION' AND page_number IS NOT NULL)
     OR (job_type = 'PAGES_IMG_AND_TEXT_EXTRACTION'         AND page_number IS NULL)),
    CONSTRAINT pages_count_positive CHECK (pages_count IS NULL OR pages_count > 0)
);

CREATE INDEX IF NOT EXISTS jobs_claimable   ON jobs (created_at) WHERE state = 'QUEUED';
CREATE INDEX IF NOT EXISTS jobs_running     ON jobs (lease_until) WHERE state = 'RUNNING';
CREATE INDEX IF NOT EXISTS jobs_terminal_age ON jobs (updated_at)
    WHERE state IN ('COMPLETED','FAILED','DEAD_LETTER');

-- job_pages: the INVARIANT-1 ledger. One row per page, enforced by the PRIMARY KEY.
CREATE TABLE IF NOT EXISTS job_pages (
    job_id         TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    page_number    INTEGER NOT NULL,
    state          TEXT NOT NULL DEFAULT 'PENDING',
    attempt        INTEGER NOT NULL DEFAULT 0,
    lease_until    TEXT,
    failure_code   TEXT,
    failure_reason TEXT,
    started_at     TEXT,
    terminal_at    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,

    PRIMARY KEY (job_id, page_number),
    CONSTRAINT page_number_positive CHECK (page_number >= 1),
    CONSTRAINT terminal_has_timestamp CHECK (
        (state IN ('REPORTED_COMPLETED','REPORTED_FAILED')) = (terminal_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS job_pages_claimable ON job_pages (job_id, page_number)
    WHERE state = 'PENDING';
CREATE INDEX IF NOT EXISTS job_pages_nonterminal ON job_pages (job_id)
    WHERE state NOT IN ('REPORTED_COMPLETED','REPORTED_FAILED');

-- artifacts: makes upload idempotent. A row exists only after confirm-upload succeeded.
CREATE TABLE IF NOT EXISTS artifacts (
    job_id          TEXT NOT NULL,
    page_number     INTEGER NOT NULL,
    artifact_type   TEXT NOT NULL,
    backend_file_id TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_in_bytes   INTEGER NOT NULL,
    confirmed_at    TEXT NOT NULL,

    PRIMARY KEY (job_id, page_number, artifact_type),
    FOREIGN KEY (job_id, page_number)
        REFERENCES job_pages(job_id, page_number) ON DELETE CASCADE,
    CONSTRAINT content_type_allowed CHECK (
        content_type IN ('image/jpeg','application/json','audio/wav','text/plain')),
    CONSTRAINT size_positive CHECK (size_in_bytes > 0)
);

-- callback_outbox: makes callbacks survive worker death. The UNIQUE constraint is what
-- makes a double `completed` for one page structurally impossible.
CREATE TABLE IF NOT EXISTS callback_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    page_number     INTEGER,
    kind            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    delivered_at    TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,

    CONSTRAINT one_callback_per_target UNIQUE (job_id, page_number, kind)
);

CREATE INDEX IF NOT EXISTS outbox_pending ON callback_outbox (next_attempt_at)
    WHERE delivered_at IS NULL;

-- At most ONE terminal callback per page. `one_callback_per_target` above stops two
-- `completed` rows; this stops a `failed` followed by a `completed`, which would be just
-- as wrong from the Backend's point of view. Transfers to Postgres unchanged.
CREATE UNIQUE INDEX IF NOT EXISTS outbox_one_terminal_per_page
    ON callback_outbox (job_id, page_number)
    WHERE kind IN ('page_completed', 'page_failed');
"""


def open_connection(path: str) -> sqlite3.Connection:
    """Open one SQLite connection with this schema applied.

    Shared by every adapter that stores state in this database -- the job store and the
    fleet-wide provider budget -- so they cannot drift on pragmas or schema version. Each
    caller gets its *own* connection: WAL plus a 30s busy timeout is what makes several
    connections (and several processes) safe against one another.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30.0)
    connection.row_factory = sqlite3.Row
    # WAL lets readers proceed while a writer holds the lock, and survives a crash
    # without losing committed transactions.
    if path != ":memory:":
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.executescript(SCHEMA)
    row = connection.execute("SELECT version FROM schema_meta").fetchone()
    if row is None:
        connection.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema version mismatch: database has {row['version']}, "
            f"this build expects {SCHEMA_VERSION}"
        )
    return connection


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SqliteJobStore:
    """Durable JobStore. Single-host; see the module docstring."""

    def __init__(
        self, path: str | Path, *, lease_seconds: int = 300, callback_backoff_base: float = 2.0
    ) -> None:
        self._path = str(path)
        self._lease_seconds = lease_seconds
        self._callback_backoff_base = callback_backoff_base
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the database and apply the schema. Idempotent."""
        async with self._lock:
            if self._connection is not None:
                return
            self._connection = await asyncio.to_thread(self._open)
            log.info("store.connected", path=self._path, backend="sqlite")

    def _open(self) -> sqlite3.Connection:
        return open_connection(self._path)

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await asyncio.to_thread(self._connection.close)
                self._connection = None

    async def __aenter__(self) -> SqliteJobStore:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── Plumbing ──────────────────────────────────────────────────────────────────

    def _require(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("store is not connected; await store.connect() first")
        return self._connection

    async def _run(self, work: Any, *args: Any) -> Any:
        """Run one unit of DB work under the lock, in a thread."""
        async with self._lock:
            return await asyncio.to_thread(work, self._require(), *args)

    # ── Jobs ──────────────────────────────────────────────────────────────────────

    async def accept(self, job: Job) -> AcceptOutcome:
        def work(connection: sqlite3.Connection) -> AcceptOutcome:
            now = _iso(_now())
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, job_type, state, file_id, folder_id, path_prefix,
                        source_url, page_number, quality_tier, payload_hash,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (job_id) DO NOTHING
                    RETURNING job_id
                    """,
                    (
                        str(job.job_id),
                        job.job_type.value,
                        JobState.QUEUED.value,
                        job.file_id,
                        job.folder_id,
                        job.path_prefix,
                        job.source_download_url,
                        job.page_number,
                        job.quality_tier.value,
                        job.payload_hash,
                        now,
                        now,
                    ),
                )
                created = cursor.fetchone() is not None
                row = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (str(job.job_id),)
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

            existing = _row_to_job(row)
            if created:
                return AcceptOutcome(job=existing, created=True)
            return AcceptOutcome(
                job=existing,
                created=False,
                conflict=existing.payload_hash != job.payload_hash,
            )

        return await self._run(work)

    async def get(self, job_id: UUID) -> Job | None:
        def work(connection: sqlite3.Connection) -> Job | None:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(row) if row else None

        return await self._run(work)

    async def running_jobs(self) -> list[Job]:
        def work(connection: sqlite3.Connection) -> list[Job]:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY created_at",
                (JobState.RUNNING.value,),
            ).fetchall()
            return [_row_to_job(row) for row in rows]

        return await self._run(work)

    async def claim_job(self, worker: str) -> Job | None:
        def work(connection: sqlite3.Connection) -> Job | None:
            now = _now()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE state = 'QUEUED'
                       OR (state = 'RUNNING'
                           AND (lease_until IS NULL OR lease_until <= ?))
                    ORDER BY created_at
                    LIMIT 1
                    """,
                    (_iso(now),),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                connection.execute(
                    """
                    UPDATE jobs
                       SET state = 'RUNNING', attempt = attempt + 1, claimed_by = ?,
                           lease_until = ?, updated_at = ?
                     WHERE job_id = ?
                    """,
                    (
                        worker,
                        _iso(now + timedelta(seconds=self._lease_seconds)),
                        _iso(now),
                        row["job_id"],
                    ),
                )
                claimed = connection.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return _row_to_job(claimed)

        return await self._run(work)

    async def set_job_state(
        self,
        job_id: UUID,
        state: JobState,
        *,
        reason: FailureReason | None = None,
        pages_count: int | None = None,
        metadata_sent: bool = False,
        defer_seconds: float | None = None,
    ) -> Job:
        def work(connection: sqlite3.Connection) -> Job:
            moment = _now()
            now = _iso(moment)
            assignments = ["state = ?", "updated_at = ?"]
            values: list[Any] = [state.value, now]
            if defer_seconds is not None:
                # Hold the lease instead of releasing it: `claim_job` skips RUNNING jobs
                # whose lease is still in the future, so this is a "come back later".
                assignments.append("lease_until = ?")
                values.append(_iso(moment + timedelta(seconds=defer_seconds)))
            if pages_count is not None:
                assignments.append("pages_count = ?")
                values.append(pages_count)
            if metadata_sent:
                assignments.append("metadata_sent_at = ?")
                values.append(now)
            if reason is not None:
                assignments.extend(["failure_code = ?", "failure_reason = ?"])
                values.extend([reason.code.value, reason.to_wire()])
            values.append(str(job_id))

            cursor = connection.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?", values
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown job {job_id}")
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
            return _row_to_job(row)

        return await self._run(work)

    # ── Page ledger ───────────────────────────────────────────────────────────────

    async def pages(self, job_id: UUID) -> list[PageTask]:
        def work(connection: sqlite3.Connection) -> list[PageTask]:
            rows = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? ORDER BY page_number",
                (str(job_id),),
            ).fetchall()
            return [_row_to_page(row) for row in rows]

        return await self._run(work)

    async def fan_out_pages(self, job_id: UUID, pages_count: int) -> list[PageTask]:
        if pages_count < 1:
            raise ValueError(f"pages_count must be >= 1, got {pages_count}")

        def work(connection: sqlite3.Connection) -> list[PageTask]:
            now = _iso(_now())
            connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    connection.execute(
                        "SELECT 1 FROM jobs WHERE job_id = ?", (str(job_id),)
                    ).fetchone()
                    is None
                ):
                    raise KeyError(f"unknown job {job_id}")
                # Every row created up front, so no page can be forgotten by omission.
                connection.executemany(
                    """
                    INSERT INTO job_pages (job_id, page_number, state, created_at, updated_at)
                    VALUES (?,?, 'PENDING', ?, ?)
                    ON CONFLICT (job_id, page_number) DO NOTHING
                    """,
                    [(str(job_id), number, now, now) for number in range(1, pages_count + 1)],
                )
                connection.execute(
                    "UPDATE jobs SET pages_count = ?, updated_at = ? WHERE job_id = ?",
                    (pages_count, now, str(job_id)),
                )
                rows = connection.execute(
                    "SELECT * FROM job_pages WHERE job_id = ? ORDER BY page_number",
                    (str(job_id),),
                ).fetchall()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return [_row_to_page(row) for row in rows]

        return await self._run(work)

    async def ensure_page(self, job_id: UUID, page_number: int) -> PageTask:
        if page_number < 1:
            raise ValueError(f"page_number must be >= 1, got {page_number}")

        def work(connection: sqlite3.Connection) -> PageTask:
            now = _iso(_now())
            connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    connection.execute(
                        "SELECT 1 FROM jobs WHERE job_id = ?", (str(job_id),)
                    ).fetchone()
                    is None
                ):
                    raise KeyError(f"unknown job {job_id}")
                connection.execute(
                    """
                    INSERT INTO job_pages (job_id, page_number, state, created_at, updated_at)
                    VALUES (?,?, 'PENDING', ?, ?)
                    ON CONFLICT (job_id, page_number) DO NOTHING
                    """,
                    (str(job_id), page_number, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM job_pages WHERE job_id = ? AND page_number = ?",
                    (str(job_id), page_number),
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return _row_to_page(row)

        return await self._run(work)

    async def claim_page(self, job_id: UUID) -> PageTask | None:
        def work(connection: sqlite3.Connection) -> PageTask | None:
            now = _now()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM job_pages
                    WHERE job_id = ?
                      AND (state = 'PENDING'
                           OR (state = 'RUNNING'
                               AND (lease_until IS NULL OR lease_until <= ?)))
                    ORDER BY page_number
                    LIMIT 1
                    """,
                    (str(job_id), _iso(now)),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                connection.execute(
                    """
                    UPDATE job_pages
                       SET state = 'RUNNING', attempt = attempt + 1,
                           started_at = COALESCE(started_at, ?),
                           lease_until = ?, updated_at = ?
                     WHERE job_id = ? AND page_number = ?
                    """,
                    (
                        _iso(now),
                        _iso(now + timedelta(seconds=self._lease_seconds)),
                        _iso(now),
                        str(job_id),
                        row["page_number"],
                    ),
                )
                claimed = connection.execute(
                    "SELECT * FROM job_pages WHERE job_id = ? AND page_number = ?",
                    (str(job_id), row["page_number"]),
                ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return _row_to_page(claimed)

        return await self._run(work)

    async def set_page_state(
        self,
        job_id: UUID,
        page_number: int,
        state: PageState,
        *,
        reason: FailureReason | None = None,
    ) -> PageTask:
        def work(connection: sqlite3.Connection) -> PageTask:
            now = _iso(_now())
            current = connection.execute(
                "SELECT state FROM job_pages WHERE job_id = ? AND page_number = ?",
                (str(job_id), page_number),
            ).fetchone()
            if current is None:
                raise KeyError(f"unknown page {job_id}/{page_number}")
            if PageState(current["state"]).is_terminal and not state.is_terminal:
                # Terminal is absorbing -- see the note in the in-memory store.
                raise TerminalPageError(
                    f"page {job_id}/{page_number} is already {current['state']} and "
                    "cannot be returned to a non-terminal state"
                )

            assignments = ["state = ?", "updated_at = ?"]
            values: list[Any] = [state.value, now]
            if state.is_terminal:
                assignments.extend(["terminal_at = ?", "lease_until = NULL"])
                values.append(now)
            else:
                # The CHECK constraint ties terminal_at to terminal states, so leaving a
                # stale timestamp behind on a reset would make the row invalid.
                assignments.append("terminal_at = NULL")
            if reason is not None:
                assignments.extend(["failure_code = ?", "failure_reason = ?"])
                values.extend([reason.code.value, reason.to_wire()])
            values.extend([str(job_id), page_number])

            cursor = connection.execute(
                f"UPDATE job_pages SET {', '.join(assignments)} "
                "WHERE job_id = ? AND page_number = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown page {job_id}/{page_number}")
            row = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? AND page_number = ?",
                (str(job_id), page_number),
            ).fetchone()
            return _row_to_page(row)

        return await self._run(work)

    # ── Artifact ledger ───────────────────────────────────────────────────────────

    async def record_artifact(self, artifact: Artifact) -> None:
        def work(connection: sqlite3.Connection) -> None:
            # ON CONFLICT DO NOTHING: the first confirmed upload wins, so a retried page
            # cannot replace a good record with a duplicate one.
            connection.execute(
                """
                INSERT INTO artifacts (
                    job_id, page_number, artifact_type, backend_file_id,
                    content_type, size_in_bytes, confirmed_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT (job_id, page_number, artifact_type) DO NOTHING
                """,
                (
                    str(artifact.job_id),
                    artifact.page_number,
                    artifact.artifact_type.value,
                    artifact.backend_file_id,
                    artifact.content_type,
                    artifact.size_in_bytes,
                    _iso(_now()),
                ),
            )

        await self._run(work)

    async def artifacts_for(self, job_id: UUID, page_number: int) -> dict[ArtifactType, Artifact]:
        def work(connection: sqlite3.Connection) -> dict[ArtifactType, Artifact]:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id = ? AND page_number = ?",
                (str(job_id), page_number),
            ).fetchall()
            return {
                ArtifactType(row["artifact_type"]): Artifact(
                    job_id=UUID(row["job_id"]),
                    page_number=row["page_number"],
                    artifact_type=ArtifactType(row["artifact_type"]),
                    backend_file_id=row["backend_file_id"],
                    size_in_bytes=row["size_in_bytes"],
                    content_type=row["content_type"],
                )
                for row in rows
            }

        return await self._run(work)

    # ── Callback outbox ───────────────────────────────────────────────────────────

    async def enqueue_callback(self, envelope: CallbackEnvelope) -> OutboxRecord:
        def work(connection: sqlite3.Connection) -> OutboxRecord:
            now = _iso(_now())
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO callback_outbox (
                        job_id, page_number, kind, payload, next_attempt_at, created_at
                    ) VALUES (?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        str(envelope.job_id),
                        envelope.page_number,
                        envelope.kind.value,
                        json.dumps(_serialise(envelope)),
                        now,
                        now,
                    ),
                )
                if envelope.is_page_terminal:
                    # Either terminal kind occupies the page's single slot.
                    row = connection.execute(
                        """
                        SELECT * FROM callback_outbox
                        WHERE job_id = ? AND page_number = ?
                          AND kind IN ('page_completed','page_failed')
                        """,
                        (str(envelope.job_id), envelope.page_number),
                    ).fetchone()
                else:
                    row = connection.execute(
                        """
                        SELECT * FROM callback_outbox
                        WHERE job_id = ? AND kind = ? AND page_number IS ?
                        """,
                        (str(envelope.job_id), envelope.kind.value, envelope.page_number),
                    ).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            return _row_to_outbox(row)

        return await self._run(work)

    async def pending_callbacks(self, limit: int = 100) -> list[OutboxRecord]:
        def work(connection: sqlite3.Connection) -> list[OutboxRecord]:
            rows = connection.execute(
                """
                SELECT * FROM callback_outbox
                WHERE delivered_at IS NULL AND next_attempt_at <= ?
                ORDER BY id
                LIMIT ?
                """,
                (_iso(_now()), limit),
            ).fetchall()
            return [_row_to_outbox(row) for row in rows]

        return await self._run(work)

    async def mark_callback_delivered(self, record_id: int) -> None:
        def work(connection: sqlite3.Connection) -> None:
            connection.execute(
                "UPDATE callback_outbox SET delivered_at = ? WHERE id = ?",
                (_iso(_now()), record_id),
            )

        await self._run(work)

    async def mark_callback_failed(self, record_id: int, error: str) -> None:
        def work(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT attempt FROM callback_outbox WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown outbox record {record_id}")
            attempt = row["attempt"] + 1
            delay = min(self._callback_backoff_base**attempt, 600)
            connection.execute(
                """
                UPDATE callback_outbox
                   SET attempt = ?, last_error = ?, next_attempt_at = ?
                 WHERE id = ?
                """,
                (attempt, error, _iso(_now() + timedelta(seconds=delay)), record_id),
            )

        await self._run(work)

    async def outbox_records(self) -> list[OutboxRecord]:
        def work(connection: sqlite3.Connection) -> list[OutboxRecord]:
            rows = connection.execute("SELECT * FROM callback_outbox ORDER BY id").fetchall()
            return [_row_to_outbox(row) for row in rows]

        return await self._run(work)

    # ── Maintenance ───────────────────────────────────────────────────────────────

    async def purge_terminal_jobs(self, older_than: timedelta) -> int:
        """Delete terminal jobs past the retention window. Children cascade."""

        def work(connection: sqlite3.Connection) -> int:
            cutoff = _iso(_now() - older_than)
            cursor = connection.execute(
                """
                DELETE FROM jobs
                WHERE state IN ('COMPLETED','FAILED','DEAD_LETTER')
                  AND updated_at < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

        return await self._run(work)

    async def clear(self) -> None:
        """Test helper: empty every table."""

        def work(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            for table in ("callback_outbox", "artifacts", "job_pages", "jobs"):
                connection.execute(f"DELETE FROM {table}")
            connection.execute("COMMIT")

        await self._run(work)


# ── Row mapping ─────────────────────────────────────────────────────────────────


def _row_to_job(row: sqlite3.Row) -> Job:
    job = Job(
        job_id=UUID(row["job_id"]),
        job_type=JobType(row["job_type"]),
        file_id=row["file_id"],
        folder_id=row["folder_id"],
        path_prefix=row["path_prefix"],
        source_download_url=row["source_url"],
        payload_hash=row["payload_hash"],
        page_number=row["page_number"],
        quality_tier=QualityTier(row["quality_tier"]),
        state=JobState(row["state"]),
        pages_count=row["pages_count"],
        metadata_sent_at=_parse(row["metadata_sent_at"]),
        attempt=row["attempt"],
        claimed_by=row["claimed_by"],
        lease_until=_parse(row["lease_until"]),
        failure_code=ErrorCode(row["failure_code"]) if row["failure_code"] else None,
        failure_reason=row["failure_reason"],
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
    )
    return job


def _row_to_page(row: sqlite3.Row) -> PageTask:
    return PageTask(
        job_id=UUID(row["job_id"]),
        page_number=row["page_number"],
        state=PageState(row["state"]),
        attempt=row["attempt"],
        lease_until=_parse(row["lease_until"]),
        failure_code=ErrorCode(row["failure_code"]) if row["failure_code"] else None,
        failure_reason=row["failure_reason"],
        started_at=_parse(row["started_at"]),
        terminal_at=_parse(row["terminal_at"]),
    )


def _serialise(envelope: CallbackEnvelope) -> dict[str, Any]:
    return {
        "job_id": str(envelope.job_id),
        "kind": envelope.kind.value,
        "page_number": envelope.page_number,
        "pages_count": envelope.pages_count,
        "artifacts": [[t.value, f] for t, f in envelope.artifacts],
        "reason": (
            {"code": envelope.reason.code.value, "detail": envelope.reason.detail}
            if envelope.reason
            else None
        ),
    }


def _deserialise(payload: dict[str, Any]) -> CallbackEnvelope:
    reason = payload.get("reason")
    return CallbackEnvelope(
        job_id=UUID(payload["job_id"]),
        kind=CallbackKind(payload["kind"]),
        page_number=payload.get("page_number"),
        pages_count=payload.get("pages_count"),
        artifacts=tuple((ArtifactType(t), f) for t, f in payload.get("artifacts") or ()),
        reason=(FailureReason(ErrorCode(reason["code"]), reason["detail"]) if reason else None),
    )


def _row_to_outbox(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        id=row["id"],
        envelope=_deserialise(json.loads(row["payload"])),
        attempt=row["attempt"],
        delivered_at=_parse(row["delivered_at"]),
        last_error=row["last_error"],
        next_attempt_at=_parse(row["next_attempt_at"]),
    )


def describe_postgres_delta() -> Iterable[str]:
    """The migration notes, as lines. Used by the docs and by `--help` output."""
    return [line for line in POSTGRES_DELTA.strip().splitlines()]
