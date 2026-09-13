"""A book that runs into a provider quota wall, and what happens next.

This is not a hypothetical failure. `README.md:491` records a real free-tier ceiling of
10 requests/day per model, and a Hume quota that ran out mid-book left
`work/المومياوات ناشيونال جيوجرافيك` with 33 page records and **zero** audio.

The behaviour under test: pages that could not be attempted are *parked*, not failed. No
callback is sent for them, the job keeps its ledger, and when the window resets the job
resumes and every page reports exactly once. INVARIANT-1 is preserved across the wall.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ai_engine.adapters.ai.ocr_textlayer import TextLayerOcr
from ai_engine.adapters.persistence import rate_budget as budget_module
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.adapters.persistence.rate_budget import InMemoryBudget
from ai_engine.domain.budget import ProviderLimit
from ai_engine.domain.enums import JobState, PageState
from ai_engine.domain.errors import ErrorCode
from ai_engine.observability.metrics import BUDGET_EXHAUSTED, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, REPO_ROOT, SAMPLE_PDF, make_job, make_settings
from tests.e2e.test_job_a_failure_paths import small_pdf_bytes
from tests.fakes.backend import FakeBackend

pytestmark = pytest.mark.skipif(
    not SAMPLE_PDF.exists(), reason="sample corpus PDF is not available"
)

WINDOW = 3600


class MeteredOcr:
    """The real free text-layer engine, declaring a paid provider.

    Real tokens, real artifacts, no network and no key -- but metered, so the budget path
    is exercised against genuine page output rather than a stub.
    """

    name = "metered-text-layer"
    provider = "gemini"

    def __init__(self) -> None:
        self._inner = TextLayerOcr(legacy_repo_root=REPO_ROOT)

    def extract_page(self, **kwargs: object):
        return self._inner.extract_page(**kwargs)


async def run_job(tmp_path: Path, fake: FakeBackend, *, budget, store, job, metrics=None):
    settings = make_settings(tmp_path)
    metrics = metrics or Metrics()
    transport = httpx.ASGITransport(app=fake.app)
    async with httpx.AsyncClient(transport=transport, base_url=BACKEND) as client:
        worker = build_worker(
            settings=settings,
            store=store,
            metrics=metrics,
            http_client=client,
            ocr=MeteredOcr(),
            budget=budget,
        )
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet(rounds=6)
    return outcome, metrics


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend(source_bytes=small_pdf_bytes(4))


async def test_a_book_that_hits_the_wall_parks_the_rest_and_reports_nothing_false(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """Two pages of allowance, four pages of book.

    The two pages that ran report normally. The two that never started must NOT be
    reported failed -- nothing was attempted, so "failed" would be a lie the Backend would
    act on. They stay PENDING and the job stays RUNNING.
    """
    metrics = Metrics()
    budget = InMemoryBudget({"gemini": ProviderLimit("gemini", 2, WINDOW)}, metrics=metrics)
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)

    outcome, _ = await run_job(tmp_path, fake, budget=budget, store=store, job=job, metrics=metrics)

    assert outcome.state is JobState.RUNNING, "a parked job is not terminal"
    assert outcome.error_code is ErrorCode.RATE_LIMITED

    reports = fake.terminal_reports()
    assert len(reports) == 2, f"only the attempted pages report, got {reports}"
    assert set(reports.values()) == {"completed"}

    pages = {page.page_number: page for page in await store.pages(job.job_id)}
    assert sum(1 for page in pages.values() if page.state is PageState.PENDING) == 2
    assert metrics.counter_value(BUDGET_EXHAUSTED, {"provider": "gemini"}) == 2

    stored = await store.get(job.job_id)
    assert stored.state is JobState.RUNNING
    assert stored.lease_until is not None, "the job must be deferred, not left claimable"


async def test_the_job_finishes_when_the_allowance_returns(
    tmp_path: Path, fake: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of parking: tomorrow the book finishes, and finishes exactly once.

    Every page ends with one terminal report -- INVARIANT-1 across a quota wall and a
    resume. The pages that already reported are not re-run, so the allowance is not spent
    twice on the same page.
    """
    budget = InMemoryBudget({"gemini": ProviderLimit("gemini", 2, WINDOW)})
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)

    start = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(budget_module, "_now", lambda: start)
    first, _metrics = await run_job(tmp_path, fake, budget=budget, store=store, job=job)
    assert first.state is JobState.RUNNING
    first_round = dict(fake.terminal_reports())

    # The window rolls over and a worker picks the job up again.
    monkeypatch.setattr(budget_module, "_now", lambda: start + timedelta(seconds=WINDOW + 1))
    second, _metrics = await run_job(tmp_path, fake, budget=budget, store=store, job=job)

    assert second.state is JobState.COMPLETED
    reports = fake.terminal_reports()
    assert sorted(reports) == [1, 2, 3, 4], "every page reported exactly once"
    assert set(reports.values()) == {"completed"}
    for page, verdict in first_round.items():
        assert reports[page] == verdict, "an already-reported page must not be revisited"

    stored = await store.get(job.job_id)
    assert stored.state is JobState.COMPLETED


async def test_a_page_that_was_parked_is_never_reported_twice_when_it_resumes(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """The fake raises on a duplicate terminal report, so this is a real assertion."""
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)

    tight = InMemoryBudget({"gemini": ProviderLimit("gemini", 1, WINDOW)})
    await run_job(tmp_path, fake, budget=tight, store=store, job=job)
    assert len(fake.terminal_reports()) == 1

    generous = InMemoryBudget({"gemini": ProviderLimit("gemini", 100, WINDOW)})
    outcome, _metrics = await run_job(tmp_path, fake, budget=generous, store=store, job=job)

    assert outcome.state is JobState.COMPLETED
    assert sorted(fake.terminal_reports()) == [1, 2, 3, 4]


async def test_a_zero_budget_parks_the_whole_book_without_touching_the_backend(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """`gemini=0/86400` is the kill switch. Metadata still goes out -- the page count is
    known and honest -- but not a single page is attempted or reported."""
    budget = InMemoryBudget({"gemini": ProviderLimit("gemini", 0, WINDOW)})
    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)

    outcome, _metrics = await run_job(tmp_path, fake, budget=budget, store=store, job=job)

    assert outcome.state is JobState.RUNNING
    assert fake.terminal_reports() == {}
    assert fake.job_failures == [], "parked is not failed"
    assert fake.metadata == {str(job.job_id): 4}
    assert fake.uploads == {}


# ── Job B: the failure that actually happened ───────────────────────────────────


class MeteredTts:
    """The deterministic synthetic voice, declaring a paid provider."""

    name = "metered-synthetic"
    provider = "hume"
    model_id = "metered-synthetic"

    def __init__(self) -> None:
        from ai_engine.adapters.ai.tts import SyntheticTts

        self._inner = SyntheticTts()

    def synthesize(self, text: str, *, voice: str | None = None):
        return self._inner.synthesize(text, voice=voice)


async def test_an_exhausted_tts_quota_parks_the_page_instead_of_losing_it(
    tmp_path: Path,
) -> None:
    """The Hume case, exactly: quota gone, and the page must not silently vanish.

    What went wrong before was 33 page records with zero audio and no report. Here the
    page is parked with no callback, the job is deferred, and the second attempt -- once
    the allowance is back -- completes it.
    """
    from tests.e2e.test_job_b_end_to_end import ARABIC_TEXT, make_audio_job, run_audio

    fake = FakeBackend(source_bytes=ARABIC_TEXT.encode("utf-8"))
    store = InMemoryJobStore()
    job = make_audio_job()
    await store.accept(job)

    exhausted = InMemoryBudget({"hume": ProviderLimit("hume", 0, WINDOW)})
    outcome, *_ = await run_audio(
        tmp_path, fake, tts=MeteredTts(), job=job, store=store, budget=exhausted
    )

    assert outcome.state is JobState.RUNNING, "parked, not terminal"
    assert outcome.error_code is ErrorCode.RATE_LIMITED
    assert fake.terminal_reports() == {}
    assert fake.job_failures == []
    assert fake.uploads == {}

    generous = InMemoryBudget({"hume": ProviderLimit("hume", 10, WINDOW)})
    outcome, *_ = await run_audio(
        tmp_path, fake, tts=MeteredTts(), job=job, store=store, budget=generous
    )

    assert outcome.state is JobState.COMPLETED
    assert fake.terminal_reports() == {job.page_number: "completed"}
