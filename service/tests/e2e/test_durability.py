"""Crash recovery against the durable store.

The in-memory store could never pass these: it loses everything on exit, so a worker that
dies mid-book takes the ledger, the artifact records and the pending callbacks with it, and
there is nothing left for a restarted worker or the reconciler to find. That is the failure
these tests exist to rule out.

Each test kills the worker at a *different* point in the page lifecycle, because the
interesting question is not "does recovery work" but "does it work from every point where
the process can die".

Note what is deliberately **not** modelled: resetting a page that already reported. A crash
cannot do that -- the process died before sending anything -- and the stores now refuse it,
because a retry of a reported page could only produce a second, contradictory report.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai_engine.adapters.persistence.sql import SqliteJobStore
from ai_engine.domain.enums import ArtifactType, JobState, JobType, PageState
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings
from tests.e2e.test_job_a_failure_paths import small_pdf_bytes
from tests.fakes.backend import FakeBackend
from tests.support import simulate_upload_then_crash, simulate_worker_crash

PAGES = 4


def make_job(file_id: str = "book-1") -> Job:
    return Job(
        job_id=uuid4(),
        job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        file_id=file_id,
        folder_id="books/g1",
        path_prefix="s3/2026/09",
        source_download_url=f"{BACKEND}/_source/{file_id}",
        payload_hash=canonical_payload_hash(
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id=file_id,
            folder_id="books/g1",
            path_prefix="s3/2026/09",
            page_number=None,
        ),
    )


async def run_worker_once(
    tmp_path: Path, fake: FakeBackend, db_path: Path, job: Job, *, accept: bool = False
):
    """One worker lifetime: open the store, run the job, close the store."""
    store = SqliteJobStore(db_path, callback_backoff_base=1.0)
    await store.connect()
    try:
        if accept:
            await store.accept(job)
        current = await store.get(job.job_id)
        assert current is not None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
        ) as client:
            worker = build_worker(
                settings=make_settings(tmp_path),
                store=store,
                metrics=Metrics(),
                http_client=client,
            )
            outcome = await worker.run_job(current)
            await worker.drain_until_quiet(rounds=8)
        return outcome
    finally:
        await store.close()


async def crash_and_requeue(db_path: Path, job: Job) -> list[int]:
    """Reopen the store, model a SIGKILL, close it again."""
    store = SqliteJobStore(db_path)
    await store.connect()
    try:
        return await simulate_worker_crash(store, job)
    finally:
        await store.close()


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend(source_bytes=small_pdf_bytes(PAGES))


@pytest.fixture
def one_page_fake() -> FakeBackend:
    """A single-page source, for tests that set up a one-page ledger.

    The job re-counts pages from the PDF on every run, so a multi-page source would
    legitimately produce more pages than such a setup claims.
    """
    return FakeBackend(source_bytes=small_pdf_bytes(1))


# ── Recovery from a mid-book death ──────────────────────────────────────────────


async def test_a_worker_that_dies_mid_book_is_resumed_by_the_next_one(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """The headline durability property.

    Worker 1 reported pages 1-2 and was SIGKILLed with page 3 claimed and page 4 not
    started. Worker 2 begins from a cold process, reads the ledger off disk, and finishes
    the book -- touching only the pages that still need work.
    """
    db = tmp_path / "jobs.db"
    job = make_job()

    store = SqliteJobStore(db, callback_backoff_base=1.0)
    await store.connect()
    try:
        await store.accept(job)
        await store.set_job_state(
            job.job_id, JobState.RUNNING, pages_count=PAGES, metadata_sent=True
        )
        await store.fan_out_pages(job.job_id, PAGES)
        for page in (1, 2):
            await store.set_page_state(job.job_id, page, PageState.REPORTED_COMPLETED)
        await store.set_page_state(job.job_id, 3, PageState.RUNNING)
    finally:
        await store.close()

    reset = await crash_and_requeue(db, job)
    assert sorted(reset) == [3, 4], "only non-terminal pages should be requeued"

    outcome = await run_worker_once(tmp_path, fake, db, job)

    assert outcome.state is JobState.COMPLETED
    assert sorted(fake.terminal_reports()) == [3, 4]
    assert len([c for c in fake.calls if c.op == "initialize_upload"]) == 4

    store = SqliteJobStore(db)
    await store.connect()
    try:
        assert all(p.is_terminal for p in await store.pages(job.job_id)), (
            "a page was left unreported"
        )
    finally:
        await store.close()


async def test_confirmed_artifacts_are_never_re_uploaded_after_a_restart(
    tmp_path: Path, one_page_fake: FakeBackend
) -> None:
    """The exact window the outbox exists for, and the expensive failure mode.

    Page 1's artifacts were confirmed and then the worker died before reporting. The
    restarted worker must skip both uploads -- re-paying for work that already landed is
    the costly mistake -- and must still report the page exactly once.
    """
    db = tmp_path / "jobs.db"
    job = make_job()

    store = SqliteJobStore(db, callback_backoff_base=1.0)
    await store.connect()
    try:
        await store.accept(job)
        await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=1, metadata_sent=True)
        await store.fan_out_pages(job.job_id, 1)
        await simulate_upload_then_crash(
            store, job, 1, artifacts=(ArtifactType.IMAGE, ArtifactType.TEXT), fake=one_page_fake
        )
        await store.set_job_state(job.job_id, JobState.QUEUED)
    finally:
        await store.close()

    outcome = await run_worker_once(tmp_path, one_page_fake, db, job)

    assert outcome.state is JobState.COMPLETED
    assert one_page_fake.terminal_reports() == {1: "completed"}
    assert [c for c in one_page_fake.calls if c.op == "initialize_upload"] == [], (
        "an already-confirmed artifact was uploaded again"
    )
    assert sorted(f for _t, f in one_page_fake.artifacts_for(1)) == [
        "pre-confirmed-image",
        "pre-confirmed-text",
    ]


async def test_a_partially_uploaded_page_uploads_only_what_is_missing(
    tmp_path: Path, one_page_fake: FakeBackend
) -> None:
    """The image confirmed, the text did not, then the worker died.

    Exactly the scenario traced in the design docs. The retry must upload the text and
    nothing else.
    """
    db = tmp_path / "jobs.db"
    job = make_job()

    store = SqliteJobStore(db, callback_backoff_base=1.0)
    await store.connect()
    try:
        await store.accept(job)
        await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=1, metadata_sent=True)
        await store.fan_out_pages(job.job_id, 1)
        await simulate_upload_then_crash(
            store, job, 1, artifacts=(ArtifactType.IMAGE,), fake=one_page_fake
        )
        await store.set_job_state(job.job_id, JobState.QUEUED)
    finally:
        await store.close()

    outcome = await run_worker_once(tmp_path, one_page_fake, db, job)

    assert outcome.state is JobState.COMPLETED
    uploaded = [c.body["contentType"] for c in one_page_fake.calls if c.op == "initialize_upload"]
    assert uploaded == ["text/plain"], f"expected only the text upload, got {uploaded}"
    types = dict(one_page_fake.artifacts_for(1))
    assert types["Image"] == "pre-confirmed-image"


async def test_pending_callbacks_are_delivered_by_a_later_worker(
    tmp_path: Path,
) -> None:
    """Artifacts landed, the report did not, and the Backend was down.

    Worker 1 uploads everything but cannot deliver the callbacks. Worker 2 starts cold,
    finds them on disk, and delivers them once the Backend recovers.
    """
    db = tmp_path / "jobs.db"
    job = make_job()
    fake = FakeBackend(source_bytes=small_pdf_bytes(2))
    fake.faults.callback_status = 503

    await run_worker_once(tmp_path, fake, db, job, accept=True)

    assert fake.page_reports == []
    store = SqliteJobStore(db)
    await store.connect()
    try:
        records = await store.outbox_records()
        assert records, "callbacks were lost rather than queued"
        assert all(not r.delivered for r in records)
    finally:
        await store.close()

    fake.faults.callback_status = None
    fake.faults.callback_failures = 0
    store = SqliteJobStore(db, callback_backoff_base=1.0)
    await store.connect()
    try:
        connection = store._require()
        connection.execute(
            "UPDATE callback_outbox SET next_attempt_at = ?",
            ("2020-01-01T00:00:00+00:00",),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
        ) as client:
            worker = build_worker(
                settings=make_settings(tmp_path),
                store=store,
                metrics=Metrics(),
                http_client=client,
            )
            await worker.drain_until_quiet(rounds=10)
        remaining = [r for r in await store.outbox_records() if not r.delivered]
    finally:
        await store.close()

    assert remaining == [], "callbacks were still undelivered after the Backend recovered"
    assert str(job.job_id) in fake.metadata


async def test_the_ledger_survives_so_the_reconciler_can_see_a_stranded_page(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """Without durability the reconciler has nothing to reconcile.

    A worker dies after claiming page 3 and never reports it. From a cold process, the
    reconciler must still find that page and force it terminal.
    """
    from ai_engine.application.reconcile import Reconciler

    db = tmp_path / "jobs.db"
    job = make_job()

    store = SqliteJobStore(db)
    await store.connect()
    try:
        await store.accept(job)
        await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=PAGES)
        await store.fan_out_pages(job.job_id, PAGES)
        for page in (1, 2):
            await store.set_page_state(job.job_id, page, PageState.REPORTED_COMPLETED)
        await store.claim_page(job.job_id)
    finally:
        await store.close()

    store = SqliteJobStore(db)
    await store.connect()
    try:
        reconciler = Reconciler(store=store, metrics=Metrics(), job_deadline_seconds=0)
        result = await reconciler.run(await store.running_jobs())

        assert result.pages_force_failed == 2  # pages 3 and 4
        assert all(p.is_terminal for p in await store.pages(job.job_id))
        forced = [r for r in await store.outbox_records() if r.envelope.page_number in (3, 4)]
        assert len(forced) == 2
        assert all("PROCESSING_TIMEOUT" in r.envelope.reason.to_wire() for r in forced)
    finally:
        await store.close()


async def test_a_duplicate_job_id_is_still_rejected_after_a_restart(
    tmp_path: Path,
) -> None:
    """Idempotency is only meaningful if it outlives the process."""
    db = tmp_path / "jobs.db"
    job = make_job()

    store = SqliteJobStore(db)
    await store.connect()
    try:
        assert (await store.accept(job)).created
    finally:
        await store.close()

    store = SqliteJobStore(db)
    await store.connect()
    try:
        outcome = await store.accept(job)
        assert not outcome.created
        assert not outcome.conflict

        other = make_job(file_id="a-different-file")
        other.job_id = job.job_id
        other.payload_hash = canonical_payload_hash(
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id="a-different-file",
            folder_id="books/g1",
            path_prefix="s3/2026/09",
            page_number=None,
        )
        assert (await store.accept(other)).conflict
    finally:
        await store.close()


async def test_a_full_book_completes_against_the_durable_store(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """The whole protocol, end to end, on disk rather than in memory."""
    db = tmp_path / "jobs.db"
    job = make_job()

    outcome = await run_worker_once(tmp_path, fake, db, job, accept=True)

    assert outcome.state is JobState.COMPLETED
    assert sorted(fake.terminal_reports()) == [1, 2, 3, 4]
    assert fake.metadata[str(job.job_id)] == PAGES

    store = SqliteJobStore(db)
    await store.connect()
    try:
        for page in range(1, PAGES + 1):
            artifacts = await store.artifacts_for(job.job_id, page)
            assert set(artifacts) == {ArtifactType.IMAGE, ArtifactType.TEXT}
        recovered = await store.get(job.job_id)
        assert recovered is not None
        assert recovered.state is JobState.COMPLETED
    finally:
        await store.close()
