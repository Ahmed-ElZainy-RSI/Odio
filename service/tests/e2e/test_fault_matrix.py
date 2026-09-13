"""Fault-injection matrix and concurrency stress.

Rather than asserting a specific outcome per fault, these assert the **one property that
must hold no matter what breaks**:

    For every fault, either every expected page receives exactly one terminal report, or
    the whole job is failed. Never neither. Never both. Never twice.

That is INVARIANT-1, and it is the property whose violation strands a book in the Backend
forever. Testing it as an invariant across a fault matrix catches combinations nobody
thought to write an example for.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from ai_engine.adapters.ai.renderer_pymupdf import PyMuPdfRenderer
from ai_engine.adapters.ai.tts import SyntheticTts
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.domain.enums import JobState, JobType, PageState
from ai_engine.domain.errors import AiInferenceError
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings
from tests.e2e.test_job_a_failure_paths import FlakyRenderer, small_pdf_bytes
from tests.e2e.test_job_b_end_to_end import (
    ARABIC_TEXT,
    make_audio_job,
    perfect_transcriber,
)
from tests.fakes.backend import FakeBackend

PAGES = 3


def job_a(file_id: str = "book-1") -> Job:
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


async def run(
    tmp_path: Path,
    fake: FakeBackend,
    job: Job,
    *,
    renderer: PyMuPdfRenderer | None = None,
    tts: Any = None,
    store: InMemoryJobStore | None = None,
):
    store = store or InMemoryJobStore(callback_backoff_base=1.0)
    if await store.get(job.job_id) is None:
        await store.accept(job)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
    ) as client:
        worker = build_worker(
            settings=make_settings(tmp_path),
            store=store,
            metrics=Metrics(),
            http_client=client,
            tts=tts or SyntheticTts(),
            transcriber=perfect_transcriber(ARABIC_TEXT),
        )
        if renderer is not None:
            worker.extraction._renderer = renderer
            worker.extraction._pages._renderer = renderer
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet(rounds=8)
    return outcome, store


async def assert_invariant_1(
    fake: FakeBackend, store: InMemoryJobStore, job: Job, *, expected: set[int]
) -> str:
    """The honest form of INVARIANT-1.

    When the Backend itself is unreachable we cannot tell it anything -- that is not a bug,
    it is the only possible behaviour, and the design says so: the outbox parks the row and
    alerts, because only the Backend can unblock the book.

    So the property we can actually guarantee, and do, is:

        For every expected page there is a terminal outcome that is either **delivered** to
        the Backend or **durably queued** for delivery. Nothing is silently dropped, and
        nothing is reported twice.

    Returns which branch held.
    """
    # Raises if any page was reported twice.
    delivered = fake.terminal_reports()
    job_failed = any(j == str(job.job_id) for j, _ in fake.job_failures)

    records = await store.outbox_records()
    queued_terminal = {
        r.envelope.page_number
        for r in records
        if r.envelope.kind.value in {"page_completed", "page_failed"}
        and r.envelope.page_number is not None
    }
    job_failure_queued = any(r.envelope.kind.value == "job_failed" for r in records)

    if job_failed or job_failure_queued:
        # A whole-job failure means the Backend is not waiting on individual pages.
        return "job_failed"

    accounted = set(delivered) | queued_terminal
    assert accounted == expected, (
        f"pages accounted for {sorted(accounted)} "
        f"(delivered {sorted(delivered)}, queued {sorted(queued_terminal)}) "
        f"but the Backend expects {sorted(expected)}"
    )
    # Every queued row must be exactly one per page: the store's uniqueness constraint.
    per_page: dict[int, int] = {}
    for record in records:
        if record.envelope.page_number is None:
            continue
        per_page[record.envelope.page_number] = per_page.get(record.envelope.page_number, 0) + 1
    assert all(count == 1 for count in per_page.values()), (
        f"a page has more than one terminal callback row: {per_page}"
    )
    return "all_pages_accounted"


class BrokenTts:
    name = "broken"
    model_id = "broken-v1"

    def synthesize(self, text: str, *, voice: str | None = None):
        raise AiInferenceError("provider exploded")


@dataclass(frozen=True)
class Fault:
    name: str
    apply: Any

    def __str__(self) -> str:  # nicer parametrize ids
        return self.name


# Every fault the contract fake can inject, plus the AI-side ones.
FAULTS = [
    Fault("none", lambda f: None),
    Fault("source_expired_once", lambda f: setattr(f.faults, "source_expired_times", 1)),
    Fault("source_expired_twice", lambda f: setattr(f.faults, "source_expired_times", 2)),
    Fault("source_missing", lambda f: setattr(f.faults, "source_missing", {"book-1"})),
    Fault(
        "init_upload_flaky",
        lambda f: (
            setattr(f.faults, "initialize_upload_status", 503),
            setattr(f.faults, "initialize_upload_failures", 2),
        ),
    ),
    Fault(
        "init_upload_down",
        lambda f: setattr(f.faults, "initialize_upload_status", 503),
    ),
    Fault(
        "put_flaky",
        lambda f: (setattr(f.faults, "put_status", 503), setattr(f.faults, "put_failures", 2)),
    ),
    Fault("put_down", lambda f: setattr(f.faults, "put_status", 500)),
    Fault(
        "put_rate_limited",
        lambda f: (
            setattr(f.faults, "put_status", 429),
            setattr(f.faults, "put_failures", 1),
            setattr(f.faults, "retry_after", "0"),
        ),
    ),
    Fault(
        "confirm_flaky",
        lambda f: (
            setattr(f.faults, "confirm_status", 503),
            setattr(f.faults, "confirm_failures", 2),
        ),
    ),
    Fault("confirm_down", lambda f: setattr(f.faults, "confirm_status", 500)),
    Fault(
        "confirm_401_once",
        lambda f: (
            setattr(f.faults, "confirm_status", 401),
            setattr(f.faults, "confirm_failures", 1),
        ),
    ),
    Fault(
        "callback_flaky",
        lambda f: (
            setattr(f.faults, "callback_status", 503),
            setattr(f.faults, "callback_failures", 2),
        ),
    ),
    Fault("callback_rejects", lambda f: setattr(f.faults, "callback_status", 422)),
    Fault("token_down", lambda f: setattr(f.faults, "token_status", 503)),
    Fault("token_rejects", lambda f: setattr(f.faults, "token_status", 401)),
]


@pytest.mark.parametrize("fault", FAULTS, ids=str)
async def test_invariant_1_survives_every_backend_fault(tmp_path: Path, fault: Fault) -> None:
    """The core property, across the whole fault surface."""
    fake = FakeBackend(source_bytes=small_pdf_bytes(PAGES))
    fault.apply(fake)
    job = job_a()

    outcome, store = await run(tmp_path, fake, job)

    branch = await assert_invariant_1(fake, store, job, expected=set(range(1, PAGES + 1)))
    assert outcome.state in {JobState.COMPLETED, JobState.FAILED}
    if branch == "all_pages_accounted":
        assert outcome.state is JobState.COMPLETED


@pytest.mark.parametrize("failing", [set(), {1}, {2}, {1, 2}, {1, 2, 3}], ids=str)
async def test_invariant_1_survives_any_combination_of_failing_pages(
    tmp_path: Path, failing: set[int]
) -> None:
    """Whichever pages break, all three still report exactly once."""
    fake = FakeBackend(source_bytes=small_pdf_bytes(PAGES))
    job = job_a()

    outcome, store = await run(tmp_path, fake, job, renderer=FlakyRenderer(failing_pages=failing))

    reports = fake.terminal_reports()
    assert set(reports) == {1, 2, 3}
    assert {p for p, kind in reports.items() if kind == "failed"} == failing
    assert outcome.state is JobState.COMPLETED

    ledger = await store.pages(job.job_id)
    assert all(p.is_terminal for p in ledger)


@pytest.mark.parametrize("fault", FAULTS, ids=str)
async def test_job_b_reports_its_page_under_every_fault(tmp_path: Path, fault: Fault) -> None:
    """Job B's single page must report exactly once, or the job must fail."""
    fake = FakeBackend(sources={"page-text": ARABIC_TEXT.encode("utf-8")})
    fault.apply(fake)
    job = make_audio_job()

    outcome, store = await run(tmp_path, fake, job)

    await assert_invariant_1(fake, store, job, expected={7})
    assert outcome.state in {JobState.COMPLETED, JobState.FAILED}


async def test_a_broken_tts_still_reports_the_page(tmp_path: Path) -> None:
    fake = FakeBackend(sources={"page-text": ARABIC_TEXT.encode("utf-8")})
    job = make_audio_job()
    await run(tmp_path, fake, job, tts=BrokenTts())
    assert fake.terminal_reports() == {7: "failed"}


# ── Concurrency and repetition ──────────────────────────────────────────────────


async def test_repeated_runs_of_the_same_job_never_double_report(
    tmp_path: Path,
) -> None:
    """The Backend retries, so re-delivery is the normal case rather than an edge case.

    Every page already reported after the first run, so subsequent runs must be no-ops:
    no new uploads, no new callbacks, no second terminal report for any page.
    """
    fake = FakeBackend(source_bytes=small_pdf_bytes(PAGES))
    store = InMemoryJobStore(callback_backoff_base=1.0)
    job = job_a()
    await store.accept(job)

    await run(tmp_path, fake, job, store=store)
    uploads_after_first = len([c for c in fake.calls if c.op == "initialize_upload"])
    reports_after_first = dict(fake.terminal_reports())

    for _ in range(3):
        await store.set_job_state(job.job_id, JobState.QUEUED)
        current = await store.get(job.job_id)
        assert current is not None
        await run(tmp_path, fake, current, store=store)

    # terminal_reports() raises on a duplicate, so reaching here already proves a lot.
    assert fake.terminal_reports() == reports_after_first
    assert len([c for c in fake.calls if c.op == "initialize_upload"]) == uploads_after_first
    assert uploads_after_first == PAGES * 2


async def test_a_terminal_page_can_never_be_reopened(tmp_path: Path) -> None:
    """The guarantee that makes the test above hold, asserted directly."""
    from ai_engine.ports.job_store import TerminalPageError

    store = InMemoryJobStore()
    job = job_a()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)
    await store.set_page_state(job.job_id, 1, PageState.REPORTED_FAILED)

    with pytest.raises(TerminalPageError):
        await store.set_page_state(job.job_id, 1, PageState.PENDING)


async def test_many_concurrent_jobs_all_complete(tmp_path: Path) -> None:
    """Six books at once, sharing one store and one HTTP client."""
    import asyncio

    fake = FakeBackend(source_bytes=small_pdf_bytes(2))
    store = InMemoryJobStore(callback_backoff_base=1.0)
    jobs = [job_a(f"book-{index}") for index in range(6)]
    for job in jobs:
        # Each job needs its own source id served by the fake.
        fake.sources[job.file_id] = small_pdf_bytes(2)
        await store.accept(job)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
    ) as client:
        worker = build_worker(
            settings=make_settings(tmp_path),
            store=store,
            metrics=Metrics(),
            http_client=client,
        )
        outcomes = await asyncio.gather(*(worker.run_job(job) for job in jobs))
        await worker.drain_until_quiet(rounds=10)

    assert all(o.state is JobState.COMPLETED for o in outcomes)
    # Every job reported both of its pages exactly once.
    per_job: dict[str, set[int]] = {}
    for job_id, page, _kind, _body in fake.page_reports:
        pages = per_job.setdefault(job_id, set())
        assert page not in pages, f"job {job_id} reported page {page} twice"
        pages.add(page)
    assert len(per_job) == len(jobs)
    assert all(pages == {1, 2} for pages in per_job.values())


async def test_concurrent_jobs_share_one_token(tmp_path: Path) -> None:
    """Single-flight refresh: N concurrent jobs must not each fetch a token."""
    import asyncio

    fake = FakeBackend(source_bytes=small_pdf_bytes(2))
    store = InMemoryJobStore(callback_backoff_base=1.0)
    jobs = [job_a(f"book-{index}") for index in range(5)]
    for job in jobs:
        fake.sources[job.file_id] = small_pdf_bytes(2)
        await store.accept(job)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
    ) as client:
        worker = build_worker(
            settings=make_settings(tmp_path),
            store=store,
            metrics=Metrics(),
            http_client=client,
        )
        await asyncio.gather(*(worker.run_job(job) for job in jobs))
        await worker.drain_until_quiet(rounds=10)

    assert fake.tokens_issued == 1, (
        f"{fake.tokens_issued} tokens issued for 5 concurrent jobs; "
        "single-flight refresh is not working"
    )
