"""Worker loop behaviour.

Driven with `max_ticks` and an injected sleep so the loop is deterministic rather than a
background task the test races.

The property that matters most here is resilience: a store outage, a drain failure or an
exception escaping the job runner must all leave the loop alive. A worker that dies on the
first transient error is worse than one that retries, because the jobs it was holding go
back to PENDING and nothing picks them up.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.application.worker_loop import WorkerLoop
from ai_engine.domain.enums import JobState, JobType
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import Metrics


@dataclass
class Drained:
    delivered: int = 0


def make_job() -> Job:
    return Job(
        job_id=uuid4(),
        job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        file_id="f",
        folder_id="d",
        path_prefix="p",
        source_download_url="https://s.example.com/x.pdf",
        payload_hash=canonical_payload_hash(
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id="f",
            folder_id="d",
            path_prefix="p",
            page_number=None,
        ),
    )


class Recorder:
    """Collects what the loop asked for."""

    def __init__(self, *, fail_on: set[int] | None = None) -> None:
        self.jobs: list[Job] = []
        self.drains = 0
        self.reconciles = 0
        self._fail_on = fail_on or set()

    async def run_job(self, job) -> object:
        self.jobs.append(job)
        if len(self.jobs) in self._fail_on:
            raise RuntimeError("job runner blew up")
        return type("Outcome", (), {"state": JobState.COMPLETED})()

    async def drain(self) -> Drained:
        self.drains += 1
        return Drained(delivered=1)

    async def reconcile(self) -> None:
        self.reconciles += 1


async def noop_sleep(seconds: float) -> None:
    return None


def build(store, recorder: Recorder, **kwargs) -> WorkerLoop:
    return WorkerLoop(
        store=store,
        run_job=recorder.run_job,
        drain=recorder.drain,
        reconcile=recorder.reconcile,
        metrics=Metrics(),
        sleep=noop_sleep,
        idle_sleep_seconds=0.0,
        **kwargs,
    )


# ── Claiming and running ────────────────────────────────────────────────────────


async def test_the_loop_claims_and_runs_a_queued_job() -> None:
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)
    recorder = Recorder()

    stats = await build(store, recorder).run(max_ticks=1)

    assert [j.job_id for j in recorder.jobs] == [job.job_id]
    assert stats.jobs_run == 1
    assert stats.errors == []


async def test_the_loop_idles_when_there_is_nothing_to_do() -> None:
    recorder = Recorder()
    stats = await build(InMemoryJobStore(), recorder).run(max_ticks=3)

    assert recorder.jobs == []
    assert stats.ticks == 3
    assert stats.jobs_run == 0
    # It still drains: a callback may be pending from a previous run or another worker.
    assert recorder.drains >= 3


async def test_the_loop_drains_the_outbox_every_tick() -> None:
    store = InMemoryJobStore()
    await store.accept(make_job())
    recorder = Recorder()
    stats = await build(store, recorder).run(max_ticks=2)
    assert stats.callbacks_delivered >= 2


async def test_multiple_jobs_are_processed_across_ticks() -> None:
    store = InMemoryJobStore()
    first, second = make_job(), make_job()
    await store.accept(first)
    await store.accept(second)
    recorder = Recorder()

    await build(store, recorder).run(max_ticks=2)

    assert {j.job_id for j in recorder.jobs} == {first.job_id, second.job_id}


# ── Shutdown ────────────────────────────────────────────────────────────────────


async def test_request_stop_ends_the_loop() -> None:
    store = InMemoryJobStore()
    recorder = Recorder()
    loop = build(store, recorder)
    loop.request_stop()

    stats = await loop.run(max_ticks=100)

    assert stats.ticks == 0
    assert loop.stopping


async def test_request_stop_is_idempotent() -> None:
    loop = build(InMemoryJobStore(), Recorder())
    loop.request_stop()
    loop.request_stop()
    assert loop.stopping


async def test_a_final_drain_runs_on_the_way_out() -> None:
    """Delivers callbacks that are already durable, so it is an optimisation not a
    correctness mechanism -- but skipping it would needlessly delay a book."""
    recorder = Recorder()
    loop = build(InMemoryJobStore(), recorder)
    await loop.run(max_ticks=1)
    # One per tick, plus the final drain.
    assert recorder.drains == 2


async def test_stopping_mid_run_still_drains() -> None:
    store = InMemoryJobStore()
    await store.accept(make_job())

    class StoppingRecorder(Recorder):
        def __init__(self, loop_ref: list) -> None:
            super().__init__()
            self._loop_ref = loop_ref

        async def run_job(self, job) -> object:
            self._loop_ref[0].request_stop()
            return await super().run_job(job)

    holder: list = []
    recorder = StoppingRecorder(holder)
    loop = build(store, recorder)
    holder.append(loop)

    stats = await loop.run(max_ticks=10)
    assert stats.jobs_run == 1
    assert recorder.drains >= 1


# ── Resilience ──────────────────────────────────────────────────────────────────


async def test_a_store_outage_does_not_kill_the_loop() -> None:
    """A worker that dies on a transient store error strands the work it was holding."""

    class BrokenStore(InMemoryJobStore):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def claim_job(self, worker: str):  # type: ignore[override]
            self.attempts += 1
            raise ConnectionError("store unreachable")

    store = BrokenStore()
    recorder = Recorder()
    stats = await build(store, recorder).run(max_ticks=3)

    assert store.attempts == 3, "the loop stopped trying"
    assert len(stats.errors) == 3
    assert all(e.startswith("claim:") for e in stats.errors)


async def test_an_exception_escaping_the_job_runner_does_not_kill_the_loop() -> None:
    """The job runner converts errors into callbacks, so reaching here is a bug -- but the
    loop must survive it and say so loudly."""
    store = InMemoryJobStore()
    await store.accept(make_job())
    await store.accept(make_job())
    recorder = Recorder(fail_on={1})

    stats = await build(store, recorder).run(max_ticks=2)

    assert stats.jobs_failed == 1
    assert any(e.startswith("run:") for e in stats.errors)
    assert len(recorder.jobs) == 2, "the second job was not attempted"


async def test_a_drain_failure_does_not_kill_the_loop() -> None:
    class BrokenDrain(Recorder):
        async def drain(self) -> Drained:
            self.drains += 1
            raise TimeoutError("backend unreachable")

    recorder = BrokenDrain()
    stats = await build(InMemoryJobStore(), recorder).run(max_ticks=2)

    assert recorder.drains >= 2
    assert any(e.startswith("drain:") for e in stats.errors)


async def test_a_reconcile_failure_does_not_kill_the_loop() -> None:
    class BrokenReconcile(Recorder):
        async def reconcile(self) -> None:
            self.reconciles += 1
            raise RuntimeError("reconcile blew up")

    recorder = BrokenReconcile()
    stats = await build(InMemoryJobStore(), recorder, reconcile_every_ticks=1).run(max_ticks=2)
    assert recorder.reconciles == 2
    assert any(e.startswith("reconcile:") for e in stats.errors)


async def test_a_failed_job_outcome_is_counted_without_being_an_error() -> None:
    """A job that fails cleanly is normal operation, not a loop error."""
    store = InMemoryJobStore()
    await store.accept(make_job())

    class FailingOutcome(Recorder):
        async def run_job(self, job) -> object:
            self.jobs.append(job)
            return type("Outcome", (), {"state": JobState.FAILED})()

    recorder = FailingOutcome()
    stats = await build(store, recorder).run(max_ticks=1)

    assert stats.jobs_run == 1
    assert stats.jobs_failed == 1
    assert stats.errors == []


# ── Reconcile scheduling ────────────────────────────────────────────────────────


async def test_reconcile_runs_on_its_interval_not_every_tick() -> None:
    recorder = Recorder()
    stats = await build(InMemoryJobStore(), recorder, reconcile_every_ticks=3).run(max_ticks=7)
    assert recorder.reconciles == 2  # ticks 3 and 6
    assert stats.reconcile_runs == 2


async def test_reconcile_can_be_disabled() -> None:
    recorder = Recorder()
    await build(InMemoryJobStore(), recorder, reconcile_every_ticks=0).run(max_ticks=5)
    assert recorder.reconciles == 0


async def test_a_loop_without_a_reconciler_still_runs() -> None:
    store = InMemoryJobStore()
    await store.accept(make_job())
    recorder = Recorder()
    loop = WorkerLoop(
        store=store,
        run_job=recorder.run_job,
        drain=recorder.drain,
        reconcile=None,
        metrics=Metrics(),
        sleep=noop_sleep,
    )
    stats = await loop.run(max_ticks=1)
    assert stats.jobs_run == 1
    assert stats.reconcile_runs == 0


async def test_queue_depth_reporting_never_raises() -> None:
    """Observability must not be able to break the work."""

    class BrokenPending(InMemoryJobStore):
        async def pending_callbacks(self, limit: int = 100):  # type: ignore[override]
            raise RuntimeError("nope")

    loop = build(BrokenPending(), Recorder())
    await loop.report_queue_depth()  # must not raise


@pytest.mark.parametrize("ticks", [0, 1, 5])
async def test_max_ticks_is_honoured_exactly(ticks: int) -> None:
    recorder = Recorder()
    stats = await build(InMemoryJobStore(), recorder).run(max_ticks=ticks)
    assert stats.ticks == ticks


# ── The reconciler role: the same loop, minus claiming ──────────────────────────


async def test_the_reconciler_role_never_claims_a_job() -> None:
    """The whole point of the flag. A reconciler that claimed jobs would compete with the
    workers for them, and the jobs it took would be run by the process least equipped to
    run them."""
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)
    recorder = Recorder()

    stats = await build(store, recorder, claim_jobs=False).run(max_ticks=3)

    assert recorder.jobs == [], "the reconciler must not run jobs"
    assert stats.jobs_run == 0
    # And the job is still there for a real worker to claim.
    assert await store.claim_job("w1") is not None


async def test_the_reconciler_role_still_sweeps_and_drains_every_tick() -> None:
    recorder = Recorder()
    stats = await build(
        InMemoryJobStore(), recorder, claim_jobs=False, reconcile_every_ticks=1
    ).run(max_ticks=4)

    assert recorder.reconciles == 4
    assert recorder.drains == 5, "four ticks plus the drain on the way out"
    assert stats.reconcile_runs == 4


async def test_the_reconciler_role_stops_on_request() -> None:
    """It has no jobs to finish, so a stop must take effect on the next tick."""
    recorder = Recorder()
    loop = build(InMemoryJobStore(), recorder, claim_jobs=False)
    loop.request_stop()
    stats = await loop.run(max_ticks=5)
    assert stats.ticks == 0
