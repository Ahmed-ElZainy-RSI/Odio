"""Job A failure paths.

These are the tests that would otherwise only fail in production, and two of them
(page isolation, crash recovery) cover the failure mode the whole design exists to
prevent: a page that never reports, which stalls a book in the Backend forever.

A smaller PDF than the happy-path suite keeps these fast.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ai_engine.adapters.ai.renderer_pymupdf import PyMuPdfRenderer
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.domain.enums import ArtifactType, JobState, PageState
from ai_engine.domain.errors import AiInferenceError, ErrorCode
from ai_engine.observability.metrics import PAGES_TOTAL, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import (
    BACKEND,
    SAMPLE_PDF,
    make_job,
    make_settings,
)
from tests.fakes.backend import FakeBackend

pytestmark = pytest.mark.skipif(
    not SAMPLE_PDF.exists(), reason="sample corpus PDF is not available"
)


def small_pdf_bytes(pages: int = 4) -> bytes:
    """A real but tiny PDF with a text layer, built in-memory."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 144), f"صفحة {index + 1} نص عربي", fontsize=18)
    payload = doc.tobytes()
    doc.close()
    return payload


class FlakyRenderer(PyMuPdfRenderer):
    """Renders normally except for chosen pages, which raise.

    Used to prove per-page isolation: one page's failure must not affect any other page,
    and must still produce exactly one terminal report.
    """

    def __init__(self, *, failing_pages: set[int]) -> None:
        super().__init__()
        self._failing = failing_pages

    def render_page(self, pdf_path: str, page_number: int) -> bytes:
        if page_number in self._failing:
            raise AiInferenceError(f"synthetic render failure on page {page_number}")
        return super().render_page(pdf_path, page_number)


async def run(
    tmp_path: Path,
    fake: FakeBackend,
    *,
    renderer: PyMuPdfRenderer | None = None,
    store: InMemoryJobStore | None = None,
    job=None,
    drain_rounds: int = 6,
    settings=None,
):
    settings = settings or make_settings(tmp_path)
    store = store or InMemoryJobStore()
    metrics = Metrics()
    job = job or make_job()
    if await store.get(job.job_id) is None:
        await store.accept(job)

    transport = httpx.ASGITransport(app=fake.app)
    async with httpx.AsyncClient(transport=transport, base_url=BACKEND) as client:
        worker = build_worker(settings=settings, store=store, metrics=metrics, http_client=client)
        if renderer is not None:
            # Swap the renderer inside the composed graph.
            worker.extraction._renderer = renderer
            worker.extraction._pages._renderer = renderer
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet(rounds=drain_rounds)
    return outcome, store, metrics, job, worker


@pytest.fixture
def small_fake() -> FakeBackend:
    return FakeBackend(source_bytes=small_pdf_bytes(4))


# ── INVARIANT-1 under failure ───────────────────────────────────────────────────


async def test_one_failed_page_does_not_stop_the_others(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """The single most important test in the suite.

    The legacy pipeline `break`s out of its page loop on the first error
    (src/pipeline/run_ocr_gemini.py:59-62), so pages after a failure never report at all.
    Here page 2 fails and pages 1, 3 and 4 must be unaffected -- and page 2 must still
    produce exactly one terminal report.
    """
    outcome, store, metrics, job, _worker = await run(
        tmp_path, small_fake, renderer=FlakyRenderer(failing_pages={2})
    )

    reports = small_fake.terminal_reports()
    assert sorted(reports) == [1, 2, 3, 4]
    assert reports[2] == "failed"
    assert reports[1] == reports[3] == reports[4] == "completed"

    # "COMPLETED" means every page reported, not every page succeeded -- REQ-META-02's own
    # semantics: "whether they fail or succeed".
    assert outcome.state is JobState.COMPLETED

    ledger = {p.page_number: p.state for p in await store.pages(job.job_id)}
    assert ledger[2] is PageState.REPORTED_FAILED
    assert all(ledger[n] is PageState.REPORTED_COMPLETED for n in (1, 3, 4))

    label = {"job_type": job.job_type.value}
    assert metrics.counter_value(PAGES_TOTAL, {**label, "outcome": "failed"}) == 1
    assert metrics.counter_value(PAGES_TOTAL, {**label, "outcome": "completed"}) == 3


async def test_every_page_failing_still_reports_all_pages(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    outcome, _store, _metrics, _job, _worker = await run(
        tmp_path, small_fake, renderer=FlakyRenderer(failing_pages={1, 2, 3, 4})
    )
    reports = small_fake.terminal_reports()
    assert sorted(reports) == [1, 2, 3, 4]
    assert set(reports.values()) == {"failed"}
    assert outcome.state is JobState.COMPLETED


async def test_failure_reason_is_safe_and_code_prefixed(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    await run(tmp_path, small_fake, renderer=FlakyRenderer(failing_pages={2}))
    reason = next(
        body["reason"]
        for _job, page, kind, body in small_fake.page_reports
        if page == 2 and kind == "failed"
    )
    assert reason.startswith("AI_INFERENCE_ERROR: ")
    assert len(reason) <= 512
    for leak in ("X-Amz-Signature", "Bearer ", "/Users/", "Traceback"):
        assert leak not in reason


# ── Crash recovery ──────────────────────────────────────────────────────────────


async def test_a_resumed_job_skips_pages_that_already_reported(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """A re-delivered job must not repeat paid work.

    Pages 1-2 already had their final word with the Backend, so a second run touches only
    pages 3-4. Re-running a reported page would repeat the AI work and its second terminal
    callback would contradict the first -- which the store and the outbox both now refuse.
    """
    store = InMemoryJobStore(callback_backoff_base=1.0)
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=4, metadata_sent=True)
    await store.fan_out_pages(job.job_id, 4)
    for page in (1, 2):
        await store.set_page_state(job.job_id, page, PageState.REPORTED_COMPLETED)
    await store.set_job_state(job.job_id, JobState.QUEUED)

    await run(tmp_path, small_fake, store=store, job=await store.get(job.job_id))

    # Only the two unreported pages were worked on: 2 pages x 2 artifacts.
    assert len([c for c in small_fake.calls if c.op == "initialize_upload"]) == 4
    assert sorted(small_fake.terminal_reports()) == [3, 4]


async def test_a_retry_after_a_crash_does_not_reupload_confirmed_artifacts(tmp_path: Path) -> None:
    """Artifacts confirmed, the report never sent, then the process died.

    The retry must reuse the recorded file ids rather than paying for the uploads again.
    """
    from tests.support import simulate_upload_then_crash

    fake = FakeBackend(source_bytes=small_pdf_bytes(1))
    store = InMemoryJobStore(callback_backoff_base=1.0)
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=1, metadata_sent=True)
    await store.fan_out_pages(job.job_id, 1)
    await simulate_upload_then_crash(
        store, job, 1, artifacts=(ArtifactType.IMAGE, ArtifactType.TEXT), fake=fake
    )
    await store.set_job_state(job.job_id, JobState.QUEUED)

    await run(tmp_path, fake, store=store, job=await store.get(job.job_id))

    assert [c for c in fake.calls if c.op == "initialize_upload"] == []
    assert fake.terminal_reports() == {1: "completed"}


async def test_a_page_gets_one_terminal_callback_even_if_both_are_attempted(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """`failed` then `completed` is a double report, and is refused.

    Keying the outbox on the callback *kind* would have allowed it, and the Backend's page
    bookkeeping would have been wrong either way.
    """
    from ai_engine.domain.callbacks import CallbackEnvelope
    from ai_engine.domain.enums import CallbackKind
    from ai_engine.domain.errors import ErrorCode, FailureReason

    store = InMemoryJobStore()
    job = make_job()
    await store.accept(job)
    await store.fan_out_pages(job.job_id, 1)

    first = await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_FAILED,
            page_number=1,
            reason=FailureReason(ErrorCode.AI_INFERENCE_ERROR, "first word"),
        )
    )
    second = await store.enqueue_callback(
        CallbackEnvelope(
            job_id=job.job_id,
            kind=CallbackKind.PAGE_COMPLETED,
            page_number=1,
            artifacts=((ArtifactType.IMAGE, "a"), (ArtifactType.TEXT, "b")),
        )
    )

    assert second.id == first.id
    records = await store.outbox_records()
    assert len(records) == 1
    assert records[0].envelope.kind is CallbackKind.PAGE_FAILED


# ── Source acquisition ──────────────────────────────────────────────────────────


async def test_expired_source_url_is_recovered(tmp_path: Path, small_fake: FakeBackend) -> None:
    """REQ-SRC-02: a 403 on a presigned URL means expired, so refresh once and retry."""
    small_fake.faults.source_expired_times = 1
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)

    assert outcome.state is JobState.COMPLETED
    assert "get_download_url" in small_fake.ops()
    assert sorted(small_fake.terminal_reports()) == [1, 2, 3, 4]


async def test_malformed_source_fails_the_whole_job_and_sends_no_page_callbacks(
    tmp_path: Path,
) -> None:
    """REQ-CB-05: "you cant parse or get the pdf" is a whole-job failure.

    Page callbacks would be incoherent here: we never established a page count, so the
    Backend has nothing to reconcile them against.
    """
    fake = FakeBackend(source_bytes=b"PK\x03\x04 this is a zip, not a pdf")
    outcome, _store, _metrics, job, _worker = await run(tmp_path, fake)

    assert outcome.state is JobState.FAILED
    assert outcome.error_code is ErrorCode.SOURCE_INVALID
    assert fake.page_reports == []
    assert "metadata" not in fake.ops()
    assert len(fake.job_failures) == 1
    job_id, reason = fake.job_failures[0]
    assert job_id == str(job.job_id)
    assert reason.startswith("SOURCE_INVALID: ")


async def test_unfetchable_source_fails_the_whole_job(tmp_path: Path) -> None:
    fake = FakeBackend(source_bytes=b"%PDF-1.4")
    fake.faults.source_missing = {"book-1"}
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, fake)

    assert outcome.state is JobState.FAILED
    assert fake.page_reports == []
    assert len(fake.job_failures) == 1


# ── Auth and transient Backend failures ─────────────────────────────────────────


async def test_expired_token_is_refreshed_and_the_call_retried(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """REQ-AUTH-04: a 401 forces one refresh and one retry."""
    small_fake.faults.confirm_status = 401
    small_fake.faults.confirm_failures = 1  # only the first confirm 401s
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)

    assert outcome.state is JobState.COMPLETED
    assert small_fake.tokens_issued >= 2, "the 401 should have forced a token refresh"
    assert sorted(small_fake.terminal_reports()) == [1, 2, 3, 4]


async def test_transient_upload_failures_are_retried(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    small_fake.faults.put_status = 503
    small_fake.faults.put_failures = 2
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)
    assert outcome.state is JobState.COMPLETED
    assert sorted(small_fake.terminal_reports()) == [1, 2, 3, 4]


async def test_retry_after_header_is_honoured(tmp_path: Path, small_fake: FakeBackend) -> None:
    small_fake.faults.put_status = 429
    small_fake.faults.put_failures = 1
    small_fake.faults.retry_after = "0"
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)
    assert outcome.state is JobState.COMPLETED


async def test_undeliverable_metadata_blocks_page_work_and_fails_the_job(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """If the Backend never learns the page count, starting page work is incoherent."""
    small_fake.faults.callback_status = 503
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)

    assert outcome.state is JobState.FAILED
    assert small_fake.page_reports == []
    # No page artifacts were uploaded, because the gate held.
    assert "initialize_upload" not in small_fake.ops()


# ── The reconciler ──────────────────────────────────────────────────────────────


async def test_reconciler_force_fails_a_page_a_dead_worker_abandoned(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """The failure the outbox cannot cover: a worker died before deciding anything.

    There is no outbox row to drain, so only an external sweep can report the page. The
    force_failed metric must move, because it is the alarm that says a worker died.
    """
    from ai_engine.application.reconcile import Reconciler
    from ai_engine.domain.enums import JobType

    store = InMemoryJobStore()
    metrics = Metrics()
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=4)
    await store.fan_out_pages(job.job_id, 4)
    # Pages 1-3 reported; page 4 was claimed and then the worker vanished.
    for page in (1, 2, 3):
        await store.set_page_state(job.job_id, page, PageState.REPORTED_COMPLETED)
    await store.claim_page(job.job_id)

    reconciler = Reconciler(store=store, metrics=metrics, job_deadline_seconds=0)
    result = await reconciler.run([await store.get(job.job_id)])

    assert result.pages_force_failed == 1
    assert (
        metrics.counter_value(
            PAGES_TOTAL,
            {"job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION.value, "outcome": "force_failed"},
        )
        == 1
    )
    records = await store.outbox_records()
    forced = [r for r in records if r.envelope.page_number == 4]
    assert len(forced) == 1
    assert forced[0].envelope.kind.value == "page_failed"
    assert "PROCESSING_TIMEOUT" in forced[0].envelope.reason.to_wire()

    ledger = {p.page_number: p.state for p in await store.pages(job.job_id)}
    assert ledger[4] is PageState.REPORTED_FAILED
    assert all(state.is_terminal for state in ledger.values())


async def test_reconciler_is_idempotent(tmp_path: Path) -> None:
    """It only ever moves non-terminal to terminal, so re-running changes nothing."""
    from ai_engine.application.reconcile import Reconciler

    store = InMemoryJobStore()
    metrics = Metrics()
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING, pages_count=2)
    await store.fan_out_pages(job.job_id, 2)

    reconciler = Reconciler(store=store, metrics=metrics, job_deadline_seconds=0)
    first = await reconciler.run([await store.get(job.job_id)])
    second = await reconciler.run([await store.get(job.job_id)])

    assert first.pages_force_failed == 2
    assert second.pages_force_failed == 0
    assert len(await store.outbox_records()) == 2


async def test_reconciler_escalates_a_job_that_never_reported_a_page_count(
    tmp_path: Path,
) -> None:
    from ai_engine.application.reconcile import Reconciler

    store = InMemoryJobStore()
    metrics = Metrics()
    job = make_job()
    await store.accept(job)
    await store.set_job_state(job.job_id, JobState.RUNNING)

    reconciler = Reconciler(store=store, metrics=metrics, job_deadline_seconds=0)
    result = await reconciler.run([await store.get(job.job_id)])

    assert result.jobs_failed == 1
    records = await store.outbox_records()
    assert [r.envelope.kind.value for r in records] == ["job_failed"]


# ── Artifact content types ──────────────────────────────────────────────────────


async def test_only_permitted_content_types_are_ever_sent(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """The fake returns 422 for anything outside the four permitted types (REQ-UPL-04),
    so completing at all proves we stayed inside the enum."""
    outcome, _store, _metrics, _job, _worker = await run(tmp_path, small_fake)
    assert outcome.state is JobState.COMPLETED
    sent = {c.body["contentType"] for c in small_fake.calls if c.op == "initialize_upload"}
    assert sent <= {"image/jpeg", "text/plain", "audio/wav", "application/json"}
    assert sent == {"image/jpeg", "text/plain"}


async def test_artifact_types_in_completed_are_exact_strings(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    await run(tmp_path, small_fake)
    types = [t for t, _ in small_fake.artifacts_for(1)]
    assert types == [ArtifactType.IMAGE.value, ArtifactType.TEXT.value]
    assert types == ["Image", "Text"]


# ── OCR engine selection ────────────────────────────────────────────────────────


async def test_a_paid_engine_without_credentials_fails_the_job_before_any_page_runs(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """A missing API key is a deployment fault, so it must cost one callback, not N.

    The engine is resolved once per document, straight after the page count and before
    fan-out. If it cannot be built, the whole job fails with a single job-level `/failed`
    -- no page is rendered, nothing is uploaded, and no page-level report is sent. The
    Backend still gets a terminal answer for the job, which is what INVARIANT-1 requires.
    """
    settings = make_settings(tmp_path).model_copy(update={"ocr_engine": "gemini"})

    outcome, store, _metrics, job, _worker = await run(tmp_path, small_fake, settings=settings)

    assert outcome.state is JobState.FAILED
    assert outcome.error_code is ErrorCode.MODEL_UNAVAILABLE
    assert small_fake.terminal_reports() == {}, "no page should have been reported"
    assert small_fake.uploads == {}, "no artifact should have been uploaded"
    assert [job_ref for job_ref, _reason in small_fake.job_failures] == [str(job.job_id)]

    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.state is JobState.FAILED


async def test_the_engine_is_resolved_once_per_document_not_once_per_page(
    tmp_path: Path, small_fake: FakeBackend
) -> None:
    """`auto` reads the PDF off disk to decide; doing that per page would be wasteful.

    Four pages, one resolution.
    """
    settings = make_settings(tmp_path).model_copy(update={"ocr_engine": "textlayer"})
    calls: list[str] = []

    outcome, _store, _metrics, _job, worker = await run(
        tmp_path, small_fake, settings=settings, job=make_job("count-engines")
    )
    assert outcome.state is JobState.COMPLETED

    # The executor caches per source path; a second prepare() for the same document must
    # hand back the identical object rather than building another engine.
    executor = worker.extraction._pages
    (source_path,) = executor._engines
    calls.append(source_path)
    assert executor.prepare(source_path) is executor._engines[source_path]
    assert len(executor._engines) == 1, "one engine per document, not one per page"
