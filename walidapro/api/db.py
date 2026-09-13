from __future__ import annotations

import json
import sqlite3

from .storage import API_ROOT, JOBS_DIR

DB_PATH = API_ROOT / "app.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipeline_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            pages_requested TEXT NOT NULL,
            dpi INTEGER NOT NULL,
            enable_final_context_review INTEGER NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_contract_jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            extra TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _import_legacy_pipeline_jobs(conn: sqlite3.Connection) -> None:
    """
    One-time import of job_meta.json files written by the pre-SQLite
    file-based store, so old jobs aren't silently lost on upgrade.
    """
    if not JOBS_DIR.exists():
        return

    for entry in JOBS_DIR.iterdir():
        meta_path = entry / "job_meta.json"

        if not meta_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        now = meta.get("updated_at") or "1970-01-01T00:00:00+00:00"

        conn.execute(
            """
            INSERT OR IGNORE INTO pipeline_jobs
                (job_id, status, pages_requested, dpi, enable_final_context_review, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meta["job_id"],
                meta.get("status", "unknown"),
                json.dumps(meta.get("pages_requested", [])),
                meta.get("dpi", 0),
                int(bool(meta.get("enable_final_context_review", False))),
                meta.get("error"),
                now,
                now,
            ),
        )


def _import_legacy_ai_contract_jobs(conn: sqlite3.Connection) -> None:
    """
    One-time import of api_output/ai_jobs/<job_id>.json files written by
    the pre-SQLite file-based store.
    """
    legacy_dir = API_ROOT / "ai_jobs"

    if not legacy_dir.exists():
        return

    for path in legacy_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        job_id = data.pop("jobId", None)
        job_type = data.pop("jobType", None)
        status = data.pop("status", "unknown")
        payload = data.pop("payload", {})

        if not job_id or not job_type:
            continue

        conn.execute(
            """
            INSERT OR IGNORE INTO ai_contract_jobs
                (job_id, job_type, status, payload, extra, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '1970-01-01T00:00:00+00:00', '1970-01-01T00:00:00+00:00')
            """,
            (
                job_id,
                job_type,
                status,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False),
            ),
        )


def init_db() -> None:
    conn = connect()

    try:
        _create_tables(conn)
        _import_legacy_pipeline_jobs(conn)
        _import_legacy_ai_contract_jobs(conn)
        conn.commit()
    finally:
        conn.close()


init_db()
