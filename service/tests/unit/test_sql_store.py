"""Durability properties, which only a persistent store can satisfy.

The contract tests in `test_job_store.py` run against every implementation. These are the
ones that are *about* durability, so they only apply here -- and they are the reason this
store exists: with the in-memory store, a worker that dies takes its in-flight jobs with
it, and there is nothing for the reconciler to find.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ai_engine.adapters.persistence.sql import (
    POSTGRES_DELTA,
    SCHEMA_VERSION,
    SqliteJobStore,
    describe_postgres_delta,
)
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import (
    ArtifactType,
    CallbackKind,
    JobState,
    JobType,
    PageState,
)
from ai_engine.domain.errors import ErrorCode, FailureReason
from ai_engine.domain.job import Artifact, Job, canonical_payload_hash


def make_job(**overrides) -> Job:
    base = {
        "job_id": uuid4(),
        "job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        "file_id": "f",
        "folder_id": "d",
        "path_prefix": "p",
        "source_download_url": "https://s.example.com/x.pdf",
    }
    base.update(overrides)
    base["payload_hash"] = canonical_payload_hash(
        job_type=base["job_type"],
        file_id=base["file_id"],
        folder_id=base["folder_id"],
        path_prefix=base["path_prefix"],
        page_number=base.get("page_number"),
    )
    return Job(**base)  # type: ignore[arg-type]


@pytest.fixture
async def db(tmp_path: Path):
    path = tmp_path / "jobs.db"
    store = SqliteJobStore(path)
    await store.connect()
    try:
        yield store, path
    finally:
        await store.close()


# ── Survival across a process restart ───────────────────────────────────────────


async def test_a_job_survives_closing_and_reopening_the_store(tmp_path: Path) -> None:
    """The whole point: a restart must not lose in-flight work."""
    path = tmp_path / "jobs.db"
    job = make_job()

    first = SqliteJobStore(path)
    await first.connect()
    await first.accept(job)
    await first.set_job_state(job.job_id, JobState.RUNNING, pages_count=3)
    await first.fan_out_pages(job.job_id, 3)
    await first.set_page_state(job.job_id, 1, PageState.REPORTED_COMPLETED)
    await first.close()

    second = SqliteJobStore(path)
    await second.connect()
    try:
        recovered = await second.get(job.job_id)
        assert recovered is not None
        assert recovered.state is JobState.RUNNING
        assert recovered.pages_count == 3

        pages = await second.pages(job.job_id)
        assert [p.page_number for p in pages] == [1, 2, 3]
        assert pages[0].state is PageState.REPORTED_COMPLETED
        assert pages[1].state is PageState.PENDING
    finally:
        await second.close()


async def test_the_outbox_survives_a_restart(tmp_path: Path) -> None:
    """Undelivered callbacks are exactly what must not be lost.

    If they were, a page whose artifacts already landed would never be reported and the
    book would stall forever -- the failure the outbox exists to prevent.
    """
    path = tmp_path / "jobs.db"
    job = make_job()

    first = SqliteJobStore(path)
    await first.connect()
    await first.accept(job)
    await first.fan_out_pages(job.job_id, 1)
    await first.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "img-1"), (ArtifactType.TEXT, "txt-1")),
        )
    )
    await first.close()

    second = SqliteJobStore(path)
    await second.connect()
    try:
        pending = await second.pending_callbacks()
        assert len(pending) == 1
        envelope = pending[0].envelope
        assert envelope.kind is CallbackKind.PAGE_COMPLETED
        assert envelope.page_number == 1
        # The payload round-trips through JSON with its types intact.
        assert envelope.artifacts == (
            (ArtifactType.IMAGE, "img-1"),
            (ArtifactType.TEXT, "txt-1"),
        )
        assert envelope.body() == {
            "artifacts": [
                {"fileId": "img-1", "artifactType": "Image"},
                {"fileId": "txt-1", "artifactType": "Text"},
            ]
        }
    finally:
        await second.close()


async def test_a_failure_reason_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    job = make_job()
    first = SqliteJobStore(path)
    await first.connect()
    await first.accept(job)
    await first.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.JOB_FAILED,
            reason=FailureReason(ErrorCode.SOURCE_INVALID, "not a PDF"),
        )
    )
    await first.close()

    second = SqliteJobStore(path)
    await second.connect()
    try:
        record = (await second.outbox_records())[0]
        assert record.envelope.reason is not None
        assert record.envelope.reason.code is ErrorCode.SOURCE_INVALID
        assert record.envelope.body() == {"reason": "SOURCE_INVALID: not a PDF"}
    finally:
        await second.close()


async def test_artifacts_survive_a_restart_so_a_retry_skips_them(
    tmp_path: Path,
) -> None:
    """This is what stops a restarted worker re-paying for work that already landed."""
    path = tmp_path / "jobs.db"
    job = make_job()
    first = SqliteJobStore(path)
    await first.connect()
    await first.accept(job)
    await first.fan_out_pages(job.job_id, 1)
    await first.record_artifact(
        Artifact(
            job_id=job.job_id,
            page_number=1,
            artifact_type=ArtifactType.IMAGE,
            backend_file_id="already-uploaded",
            size_in_bytes=1234,
        )
    )
    await first.close()

    second = SqliteJobStore(path)
    await second.connect()
    try:
        artifacts = await second.artifacts_for(job.job_id, 1)
        assert artifacts[ArtifactType.IMAGE].backend_file_id == "already-uploaded"
        assert artifacts[ArtifactType.IMAGE].content_type == "image/jpeg"
    finally:
        await second.close()


# ── Concurrency ─────────────────────────────────────────────────────────────────


async def test_concurrent_claims_hand_each_job_to_exactly_one_worker(db) -> None:
    store, _path = db
    jobs = [make_job() for _ in range(8)]
    for job in jobs:
        await store.accept(job)

    claimed = await asyncio.gather(*(store.claim_job(f"worker-{index}") for index in range(12)))
    ids = [job.job_id for job in claimed if job is not None]

    assert len(ids) == 8
    assert len(set(ids)) == 8, "a job was claimed twice"
    assert {job.job_id for job in jobs} == set(ids)


async def test_concurrent_accepts_of_one_job_id_create_one_row(db) -> None:
    """The durable equivalent of the in-memory idempotency guarantee."""
    store, _path = db
    job = make_job()
    outcomes = await asyncio.gather(*(store.accept(job) for _ in range(20)))

    assert sum(1 for o in outcomes if o.created) == 1
    assert sum(1 for o in outcomes if not o.created) == 19
    assert not any(o.conflict for o in outcomes)


async def test_concurrent_page_claims_are_exclusive(db) -> None:
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 6)

    claimed = await asyncio.gather(*(store.claim_page(job.job_id) for _ in range(10)))
    numbers = [page.page_number for page in claimed if page is not None]

    assert sorted(numbers) == [1, 2, 3, 4, 5, 6]
    assert len(set(numbers)) == 6


async def test_concurrent_enqueues_of_one_callback_create_one_row(db) -> None:
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    envelope = CallbackEnvelope(
        job_id=job.job_id,
        kind=CallbackKind.PAGE_COMPLETED,
        page_number=1,
        artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
    )

    records = await asyncio.gather(*(store.enqueue_callback(envelope) for _ in range(15)))

    assert len({record.id for record in records}) == 1
    assert len(await store.outbox_records()) == 1


# ── Schema integrity ────────────────────────────────────────────────────────────


async def test_the_page_number_check_constraint_is_enforced(db) -> None:
    """A constraint that only exists in the docs is not a constraint."""
    store, _path = db
    job = make_job()
    await store.accept(job)
    with pytest.raises(ValueError, match="page_number"):
        await store.ensure_page(job.job_id, 0)


async def test_an_unknown_content_type_is_rejected_by_the_database(db) -> None:
    """REQ-UPL-04, enforced in the schema rather than only in Python."""
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    connection = store._require()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO artifacts (job_id, page_number, artifact_type, backend_file_id,
                                   content_type, size_in_bytes, confirmed_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (str(job.job_id), 1, "Audio", "f", "audio/mpeg", 10, "2026-01-01T00:00:00Z"),
        )


async def test_a_second_callback_of_the_same_kind_is_rejected_by_the_database(db) -> None:
    """`one_callback_per_target`: the same kind cannot be enqueued twice."""
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
        )
    )

    connection = store._require()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO callback_outbox (job_id, page_number, kind, payload,
                                         next_attempt_at, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (str(job.job_id), 1, "page_completed", "{}", "x", "x"),
        )


async def test_deleting_a_job_cascades_to_its_children(db) -> None:
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 2)
    await store.record_artifact(
        Artifact(
            job_id=job.job_id,
            page_number=1,
            artifact_type=ArtifactType.TEXT,
            backend_file_id="t",
            size_in_bytes=5,
        )
    )
    await store.enqueue_callback(
        CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.METADATA, pages_count=2)
    )

    connection = store._require()
    connection.execute("DELETE FROM jobs WHERE job_id = ?", (str(job.job_id),))

    assert await store.pages(job.job_id) == []
    assert await store.artifacts_for(job.job_id, 1) == {}
    assert await store.outbox_records() == []


async def test_a_schema_version_mismatch_refuses_to_open(tmp_path: Path) -> None:
    """Silently running against a schema you do not understand corrupts data."""
    path = tmp_path / "jobs.db"
    store = SqliteJobStore(path)
    await store.connect()
    connection = store._require()
    connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION + 99,))
    await store.close()

    with pytest.raises(RuntimeError, match="schema version mismatch"):
        await SqliteJobStore(path).connect()


async def test_using_the_store_before_connecting_is_a_clear_error(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.db")
    with pytest.raises(RuntimeError, match="not connected"):
        await store.get(uuid4())


async def test_connect_is_idempotent(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.db")
    await store.connect()
    await store.connect()
    try:
        assert await store.get(uuid4()) is None
    finally:
        await store.close()


async def test_the_store_works_as_an_async_context_manager(tmp_path: Path) -> None:
    job = make_job()
    async with SqliteJobStore(tmp_path / "jobs.db") as store:
        outcome = await store.accept(job)
        assert outcome.created


# ── Page state resets ───────────────────────────────────────────────────────────


async def test_resetting_a_non_terminal_page_clears_any_stale_timestamp(db) -> None:
    """The CHECK constraint ties `terminal_at` to terminal states, so a reset that left a
    stale timestamp behind would make the row invalid and the retry impossible."""
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    await store.set_page_state(job.job_id, 1, PageState.ARTIFACTS_UPLOADED)
    await store.set_page_state(job.job_id, 1, PageState.PENDING)
    reset = (await store.pages(job.job_id))[0]
    assert reset.state is PageState.PENDING
    assert reset.terminal_at is None


async def test_a_terminal_page_cannot_be_reset_even_in_the_database(db) -> None:
    """Enforced by the store, so a retry cannot produce a second report."""
    from ai_engine.ports.job_store import TerminalPageError

    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.set_page_state(job.job_id, 1, PageState.REPORTED_FAILED)

    with pytest.raises(TerminalPageError):
        await store.set_page_state(job.job_id, 1, PageState.RUNNING)


async def test_the_schema_forbids_two_terminal_callbacks_for_one_page(db) -> None:
    """A partial unique index, not just application logic.

    `one_callback_per_target` stops two `completed` rows; this stops a `failed` followed by
    a `completed`. Both are double reports as far as the Backend is concerned.
    """
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
        )
    )

    connection = store._require()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO callback_outbox (job_id, page_number, kind, payload,
                                         next_attempt_at, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (str(job.job_id), 1, "page_failed", "{}", "x", "x"),
        )


# ── Retention ───────────────────────────────────────────────────────────────────


async def test_purge_removes_only_old_terminal_jobs(db) -> None:
    store, _path = db
    old_done = make_job()
    recent_done = make_job()
    still_running = make_job()
    for job in (old_done, recent_done, still_running):
        await store.accept(job)
    await store.set_job_state(old_done.job_id, JobState.COMPLETED)
    await store.set_job_state(recent_done.job_id, JobState.COMPLETED)
    await store.set_job_state(still_running.job_id, JobState.RUNNING)

    # Backdate one job past the window.
    connection = store._require()
    connection.execute(
        "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
        ("2020-01-01T00:00:00+00:00", str(old_done.job_id)),
    )

    purged = await store.purge_terminal_jobs(timedelta(days=90))

    assert purged == 1
    assert await store.get(old_done.job_id) is None
    assert await store.get(recent_done.job_id) is not None
    assert await store.get(still_running.job_id) is not None


async def test_purge_never_removes_a_running_job(db) -> None:
    store, _path = db
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING)
    connection = store._require()
    connection.execute(
        "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
        ("2020-01-01T00:00:00+00:00", str(job.job_id)),
    )

    assert await store.purge_terminal_jobs(timedelta(days=1)) == 0
    assert await store.get(job.job_id) is not None


# ── Job B shape ─────────────────────────────────────────────────────────────────


async def test_a_job_b_row_requires_a_page_number(db) -> None:
    """Enforced by a CHECK constraint, not only by the domain model."""
    store, _path = db
    connection = store._require()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO jobs (job_id, job_type, state, file_id, folder_id, path_prefix,
                              source_url, page_number, quality_tier, payload_hash,
                              created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid4()),
                "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
                "QUEUED",
                "f",
                "d",
                "p",
                "u",
                None,
                "fast",
                "h",
                "x",
                "x",
            ),
        )


async def test_a_job_a_row_must_not_carry_a_page_number(db) -> None:
    store, _path = db
    connection = store._require()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO jobs (job_id, job_type, state, file_id, folder_id, path_prefix,
                              source_url, page_number, quality_tier, payload_hash,
                              created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid4()),
                "PAGES_IMG_AND_TEXT_EXTRACTION",
                "QUEUED",
                "f",
                "d",
                "p",
                "u",
                3,
                "fast",
                "h",
                "x",
                "x",
            ),
        )


async def test_job_b_single_page_ledger_round_trips(db) -> None:
    store, _path = db
    job = make_job(job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION, page_number=7)
    await store.accept(job)
    await store.ensure_page(job.job_id, 7)

    pages = await store.pages(job.job_id)
    assert [p.page_number for p in pages] == [7]
    recovered = await store.get(job.job_id)
    assert recovered is not None
    assert recovered.page_number == 7


# ── The migration notes ─────────────────────────────────────────────────────────


def test_the_postgres_delta_is_documented_in_code() -> None:
    """Kept in code so it cannot drift away from the schema it describes."""
    assert "FOR UPDATE SKIP LOCKED" in POSTGRES_DELTA
    assert "ON CONFLICT DO NOTHING" in POSTGRES_DELTA
    assert len(list(describe_postgres_delta())) > 5
