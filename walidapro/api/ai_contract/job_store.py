from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .. import db

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_job(job_id: str, job_type: str, payload: dict) -> bool:
    """
    Idempotency guard. Returns True if this jobId is genuinely new (caller
    should enqueue background work); False if it was already seen (caller
    should just return 202 without doing anything again).
    """
    now = _now()

    with _LOCK:
        conn = db.connect()
        try:
            existing = conn.execute(
                "SELECT 1 FROM ai_contract_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

            if existing is not None:
                return False

            conn.execute(
                """
                INSERT INTO ai_contract_jobs
                    (job_id, job_type, status, payload, extra, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, '{}', ?, ?)
                """,
                (job_id, job_type, json.dumps(payload, ensure_ascii=False), now, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def update_status(job_id: str, status: str, extra: dict | None = None) -> None:
    with _LOCK:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT extra FROM ai_contract_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

            current_extra = json.loads(row["extra"]) if row is not None else {}

            if extra:
                current_extra.update(extra)

            conn.execute(
                """
                UPDATE ai_contract_jobs
                SET status = ?, extra = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, json.dumps(current_extra, ensure_ascii=False), _now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_job(job_id: str) -> dict | None:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM ai_contract_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "jobId": row["job_id"],
        "jobType": row["job_type"],
        "status": row["status"],
        "payload": json.loads(row["payload"]),
        **json.loads(row["extra"]),
    }
