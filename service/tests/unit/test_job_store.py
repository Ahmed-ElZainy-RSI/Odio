"""JobStore atomicity contracts, run against EVERY implementation.

Written against the port's *documented behaviour* rather than any implementation's
internals, and parametrized over both stores. That is what makes them acceptance criteria:
a new adapter is correct when these pass unchanged.

Currently covered:

* :class:`InMemoryJobStore` -- fast, non-durable, single process.
* :class:`SqliteJobStore` -- durable, restart-safe, single host.

A Postgres adapter (`T-4.1` proper) should be added to `STORES` and nothing else should
change. `ai_engine.adapters.persistence.sql.POSTGRES_DELTA` records exactly what differs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.adapters.persistence.sql import SqliteJobStore
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.enums import (
    ArtifactType,
    CallbackKind,
    JobState,
    JobType,
    PageState,
)
from ai_engine.domain.job import Artifact, Job, canonical_payload_hash

# ── The store fixture: every contract test runs against every implementation ────

STORES = ["memory", "sqlite"]


@pytest.fixture(params=STORES)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator:
    """One connected, empty store per implementation."""
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    sqlite_store = SqliteJobStore(tmp_path / "jobs.db")
    await sqlite_store.connect()
    try:
        yield sqlite_store
    finally:
        await sqlite_store.close()


@pytest.fixture
async def fast_store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator:
    """A store with a zero lease, for reclaim tests."""
    yield None


async def zero_lease_store(kind: str, tmp_path: Path):
    """A store whose leases are already expired, for reclaim tests."""
    if kind == "memory":
        return InMemoryJobStore(lease_seconds=0)
    store = SqliteJobStore(tmp_path / f"{kind}-zero.db", lease_seconds=0)
    await store.connect()
    return store


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


# ── Claiming ────────────────────────────────────────────────────────────────────


async def test_two_workers_claim_different_jobs(store) -> None:
    """A claimed job must be handed to exactly one worker.

    In Postgres this is `SELECT ... FOR UPDATE SKIP LOCKED`; the in-memory store holds a
    lock across the read-and-update to give the same guarantee.
    """
    first, second = make_job(), make_job()
    await store.accept(first)
    await store.accept(second)

    claimed = await asyncio.gather(store.claim_job("w1"), store.claim_job("w2"))
    ids = {job.job_id for job in claimed if job is not None}

    assert len(ids) == 2, "two workers claimed the same job"
    assert ids == {first.job_id, second.job_id}
    assert await store.claim_job("w3") is None, "a third worker got a claimed job"


async def test_a_claimed_job_is_not_reclaimable_while_its_lease_holds(store) -> None:
    await store.accept(make_job())
    assert await store.claim_job("w1") is not None
    assert await store.claim_job("w2") is None


@pytest.mark.parametrize("kind", STORES)
async def test_expired_lease_is_reclaimable(kind: str, tmp_path: Path) -> None:
    """A worker that dies without failing must not strand its job.

    An expired lease means the worker is presumed dead, so the job becomes claimable again
    with `attempt` already incremented -- no distributed lock, just a timestamp.
    """
    store = await zero_lease_store(kind, tmp_path)
    job = make_job()
    await store.accept(job)

    first = await store.claim_job("w1")
    assert first is not None
    assert first.attempt == 1

    second = await store.claim_job("w2")
    assert second is not None
    assert second.job_id == job.job_id
    assert second.attempt == 2, "the attempt counter must advance on reclaim"
    assert second.claimed_by == "w2"


async def test_page_claims_are_exclusive_and_ordered(store) -> None:
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 3)

    claims = [await store.claim_page(job.job_id) for _ in range(4)]
    numbers = [c.page_number for c in claims if c is not None]

    assert numbers == [1, 2, 3], "pages should be handed out once each, in order"
    assert claims[3] is None


@pytest.mark.parametrize("kind", STORES)
async def test_expired_page_lease_is_reclaimable(kind: str, tmp_path: Path) -> None:
    store = await zero_lease_store(kind, tmp_path)
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    first = await store.claim_page(job.job_id)
    second = await store.claim_page(job.job_id)
    assert first is not None and second is not None
    assert second.page_number == 1
    assert second.attempt == 2


@pytest.mark.parametrize("kind", STORES)
async def test_a_terminal_page_is_never_reclaimed(kind: str, tmp_path: Path) -> None:
    store = await zero_lease_store(kind, tmp_path)
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.set_page_state(job.job_id, 1, PageState.REPORTED_COMPLETED)

    assert await store.claim_page(job.job_id) is None


# ── Fan-out ─────────────────────────────────────────────────────────────────────


async def test_fan_out_creates_exactly_pages_count_rows(store) -> None:
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 30)

    pages = await store.pages(job.job_id)
    assert [p.page_number for p in pages] == list(range(1, 31))
    assert all(p.state is PageState.PENDING for p in pages)


async def test_fan_out_is_idempotent(store) -> None:
    """A re-run must not duplicate ledger rows or reset progress."""
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 3)
    await store.set_page_state(job.job_id, 2, PageState.REPORTED_COMPLETED)
    await store.fan_out_pages(job.job_id, 3)

    pages = {p.page_number: p.state for p in await store.pages(job.job_id)}
    assert len(pages) == 3
    assert pages[2] is PageState.REPORTED_COMPLETED


async def test_fan_out_rejects_a_non_positive_count(store) -> None:
    job = make_job()
    await store.accept(job)
    with pytest.raises(ValueError, match="pages_count"):
        await store.fan_out_pages(job.job_id, 0)


# ── Artifact ledger ─────────────────────────────────────────────────────────────


async def test_first_confirmed_artifact_wins(store) -> None:
    """A retried page must not replace a good record with a duplicate one."""
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    for file_id in ("first", "second"):
        await store.record_artifact(
            Artifact(
                job_id=job.job_id,
                page_number=1,
                artifact_type=ArtifactType.IMAGE,
                backend_file_id=file_id,
                size_in_bytes=10,
            )
        )

    artifacts = await store.artifacts_for(job.job_id, 1)
    assert artifacts[ArtifactType.IMAGE].backend_file_id == "first"


async def test_artifacts_are_scoped_to_their_page(store) -> None:
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 2)
    await store.record_artifact(
        Artifact(
            job_id=job.job_id,
            page_number=1,
            artifact_type=ArtifactType.TEXT,
            backend_file_id="p1",
            size_in_bytes=5,
        )
    )
    assert set(await store.artifacts_for(job.job_id, 1)) == {ArtifactType.TEXT}
    assert await store.artifacts_for(job.job_id, 2) == {}


# ── Outbox ──────────────────────────────────────────────────────────────────────


async def test_one_outbox_row_per_logical_callback(store) -> None:
    """Makes a double `completed` structurally impossible, however often a page re-runs."""
    job = make_job()
    await store.accept(job)
    envelope = CallbackEnvelope(
        job_id=job.job_id,
        kind=CallbackKind.PAGE_COMPLETED,
        page_number=1,
        artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
    )

    first = await store.enqueue_callback(envelope)
    second = await store.enqueue_callback(envelope)

    assert first.id == second.id
    assert len(await store.outbox_records()) == 1


async def test_a_page_gets_exactly_one_terminal_callback_slot(store) -> None:
    """`page_completed` and `page_failed` share one slot per page.

    Keying the outbox on the *kind* would let a page be reported failed and then completed.
    From the Backend's point of view that is just as much a double report as two
    `completed` calls -- their page bookkeeping would be wrong either way. A page gets one
    final word, and the first one wins.
    """
    from ai_engine.domain.errors import ErrorCode, FailureReason

    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    completed = await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
        )
    )
    failed = await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_FAILED,
            page_number=1,
            reason=FailureReason(ErrorCode.AI_INFERENCE_ERROR, "too late"),
        )
    )

    assert failed.id == completed.id, "a second terminal callback took a new slot"
    records = await store.outbox_records()
    assert len(records) == 1
    assert records[0].envelope.kind is CallbackKind.PAGE_COMPLETED


async def test_a_metadata_callback_does_not_occupy_a_page_slot(store) -> None:
    """Only page-terminal kinds collapse; job-scoped ones are independent."""
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    await store.enqueue_callback(
        CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.METADATA, pages_count=1)
    )
    await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
        )
    )
    assert len(await store.outbox_records()) == 2


async def test_a_terminal_page_cannot_be_returned_to_pending(store) -> None:
    """Terminal is absorbing, and the store enforces it.

    A page that has reported is finished. Allowing a reset would let a retry produce a
    second, contradictory report -- exactly what INVARIANT-1 forbids.
    """
    from ai_engine.ports.job_store import TerminalPageError

    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.set_page_state(job.job_id, 1, PageState.REPORTED_COMPLETED)

    with pytest.raises(TerminalPageError, match="cannot be returned"):
        await store.set_page_state(job.job_id, 1, PageState.PENDING)


async def test_a_terminal_page_may_still_be_re_marked_terminal(store) -> None:
    """Idempotent re-marking is harmless and keeps the reconciler simple."""
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.set_page_state(job.job_id, 1, PageState.REPORTED_COMPLETED)
    again = await store.set_page_state(job.job_id, 1, PageState.REPORTED_COMPLETED)
    assert again.state is PageState.REPORTED_COMPLETED


async def test_delivered_rows_leave_the_pending_set(store) -> None:
    job = make_job()
    await store.accept(job)
    record = await store.enqueue_callback(
        CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.METADATA, pages_count=3)
    )
    assert len(await store.pending_callbacks()) == 1
    await store.mark_callback_delivered(record.id)
    assert await store.pending_callbacks() == []


async def test_a_failed_callback_is_backed_off_not_dropped(store) -> None:
    job = make_job()
    await store.accept(job)
    record = await store.enqueue_callback(
        CallbackEnvelope(job_id=job.job_id, kind=CallbackKind.METADATA, pages_count=3)
    )
    await store.mark_callback_failed(record.id, "HTTP 503")

    stored = (await store.outbox_records())[0]
    assert stored.attempt == 1
    assert stored.last_error == "HTTP 503"
    assert not stored.delivered
    # Backed off, so it is not immediately re-attempted.
    assert await store.pending_callbacks() == []


# ── Job state ───────────────────────────────────────────────────────────────────


async def test_metadata_sent_is_recorded_and_opens_the_gate(store) -> None:
    job = make_job()
    await store.accept(job)
    assert not job.may_start_page_work

    updated = await store.set_job_state(
        job.job_id, JobState.RUNNING, pages_count=4, metadata_sent=True
    )
    assert updated.pages_count == 4
    assert updated.metadata_sent_at is not None
    assert updated.may_start_page_work


async def test_failure_reason_is_stored_on_the_wire_form(store) -> None:
    from ai_engine.domain.errors import ErrorCode, FailureReason

    job = make_job()
    await store.accept(job)
    reason = FailureReason(ErrorCode.SOURCE_INVALID, "not a PDF")
    updated = await store.set_job_state(job.job_id, JobState.FAILED, reason=reason)

    assert updated.failure_code is ErrorCode.SOURCE_INVALID
    assert updated.failure_reason == "SOURCE_INVALID: not a PDF"


# ── Deferral: how a job waits for a provider window to reset ────────────────────


async def test_a_deferred_job_is_not_claimable_until_its_deferral_expires(store) -> None:
    """`defer_seconds` is the parking mechanism for an exhausted provider budget.

    Without it a job parked behind a 24-hour quota would be re-claimed every few minutes,
    fail the same way every time, and fill the logs with nothing.
    """
    job = make_job()
    await store.accept(job)
    assert await store.claim_job("w1") is not None

    await store.set_job_state(job.job_id, JobState.RUNNING, defer_seconds=3600)
    assert await store.claim_job("w2") is None, "a deferred job must stay invisible"


async def test_a_deferral_that_has_elapsed_makes_the_job_claimable_again(store) -> None:
    job = make_job()
    await store.accept(job)
    await store.claim_job("w1")

    await store.set_job_state(job.job_id, JobState.RUNNING, defer_seconds=0)
    reclaimed = await store.claim_job("w2")
    assert reclaimed is not None and reclaimed.job_id == job.job_id


async def test_a_state_change_without_a_deferral_leaves_the_lease_alone(store) -> None:
    """Only an explicit `defer_seconds` should move the lease; everything else is a
    state change and must not accidentally extend or release a claim."""
    job = make_job()
    await store.accept(job)
    await store.claim_job("w1")

    await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=4)
    assert await store.claim_job("w2") is None, "the original lease should still hold"
