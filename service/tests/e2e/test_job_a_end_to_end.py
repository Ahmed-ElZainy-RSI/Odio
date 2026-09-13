"""Job A, end to end, against the contract fake.

This is the vertical slice: a real PDF from the corpus goes in, and the mandated protocol
comes out -- metadata, then per page an image upload, a text upload and one terminal
report. Zero network calls, zero paid API calls: the source PDF uses the free text-layer
path, and every Backend/Keycloak/storage interaction is served by the fake over an ASGI
transport.

The assertions here are the ones that would otherwise only fail during a joint debugging
session with the Backend team.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.config.settings import Settings
from ai_engine.domain.enums import ArtifactType, JobState, JobType, PageState
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import PAGES_TOTAL, Metrics
from ai_engine.worker import build_worker
from tests.fakes.backend import FakeBackend

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PDF = REPO_ROOT / "assets" / "PDF Samples" / "النجوم.pdf"
BACKEND = "https://fake-backend.test"

pytestmark = [
    pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="sample corpus PDF is not available"),
    # 16 real pages rendered at 2x: correct, but not a fast-feedback test.
    pytest.mark.slow,
]


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="WARNING",
        backend_base_url=BACKEND,
        token_url=f"{BACKEND}/realms/reading-first/protocol/openid-connect/token",
        ai_engine_client_id="reading-first-ocr-engine",
        ai_engine_client_secret="fake-secret",
        source_url_allowed_hosts=["fake-backend.test"],
        # Behind an ASGI transport there is nothing to resolve. The control itself is
        # tested directly in tests/unit, and its default is asserted in tests/api.
        source_url_resolve_and_check=False,
        scratch_dir=tmp_path / "scratch",
        legacy_repo_root=REPO_ROOT,
        job_page_concurrency=4,
        # Pinned rather than `auto` so no test can ever reach a paid provider. `auto`'s
        # own resolution is covered by test_ocr_providers and by
        # test_auto_resolves_to_the_free_text_layer_for_a_book_that_has_one below.
        ocr_engine="textlayer",
        # Real backoff would dominate the suite's runtime without changing what it
        # proves; the retry policy itself is exhaustively tested in unit tests.
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.01,
        retry_total_budget_seconds=2.0,
    )


def make_job(file_id: str = "book-1") -> Job:
    return Job(
        job_id=uuid4(),
        job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        file_id=file_id,
        folder_id="books/reading-first/grade-1",
        path_prefix="s3-prefix/2026/09",
        source_download_url=f"{BACKEND}/_source/{file_id}",
        payload_hash=canonical_payload_hash(
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id=file_id,
            folder_id="books/reading-first/grade-1",
            path_prefix="s3-prefix/2026/09",
            page_number=None,
        ),
    )


async def run_job(
    tmp_path: Path, fake: FakeBackend, job: Job | None = None
) -> tuple[object, InMemoryJobStore, Metrics, Job]:
    settings = make_settings(tmp_path)
    store = InMemoryJobStore()
    metrics = Metrics()
    job = job or make_job()
    await store.accept(job)

    transport = httpx.ASGITransport(app=fake.app)
    async with httpx.AsyncClient(transport=transport, base_url=BACKEND) as client:
        worker = build_worker(settings=settings, store=store, metrics=metrics, http_client=client)
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet()
    return outcome, store, metrics, job


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend(source_bytes=SAMPLE_PDF.read_bytes())


# ── The happy path ──────────────────────────────────────────────────────────────


async def test_job_a_completes_every_page(tmp_path: Path, fake: FakeBackend) -> None:
    outcome, store, _metrics, job = await run_job(tmp_path, fake)

    assert outcome.state is JobState.COMPLETED
    assert outcome.pages_count == 16  # النجوم has 16 pages
    assert fake.metadata[str(job.job_id)] == 16

    # INVARIANT-1: exactly one terminal report per page in 1..N, no duplicates.
    reports = fake.terminal_reports()
    assert sorted(reports) == list(range(1, 17))
    assert set(reports.values()) == {"completed"}

    ledger = await store.pages(job.job_id)
    assert all(p.state is PageState.REPORTED_COMPLETED for p in ledger)


async def test_metadata_is_sent_before_any_upload(tmp_path: Path, fake: FakeBackend) -> None:
    """REQ-META-01: the page count reaches the Backend before page work."""
    await run_job(tmp_path, fake)
    ops = fake.ops()
    assert "metadata" in ops
    assert ops.index("metadata") < ops.index("initialize_upload")


async def test_upload_order_is_image_then_text(tmp_path: Path, fake: FakeBackend) -> None:
    """REQ-UPL-05, and REQ-CB-01: both files confirmed before `completed`."""
    _outcome, _store, _metrics, _job = await run_job(tmp_path, fake)

    # Content types, in the order initialize-upload was called.
    inits = [c.body["contentType"] for c in fake.calls if c.op == "initialize_upload"]
    assert inits[:2] == ["image/jpeg", "text/plain"]
    assert inits.count("image/jpeg") == 16
    assert inits.count("text/plain") == 16

    # The completed payload lists them in production order.
    assert [t for t, _ in fake.artifacts_for(3)] == ["Image", "Text"]

    # And per page: three upload steps for each of two files, then the report.
    page_ops = [
        c.op
        for c in fake.calls
        if c.op in {"initialize_upload", "put_upload", "confirm_upload", "page_completed"}
    ]
    first_report = page_ops.index("page_completed")
    assert page_ops[:first_report].count("confirm_upload") >= 2


async def test_completed_only_references_confirmed_artifacts(
    tmp_path: Path, fake: FakeBackend
) -> None:
    """The fake rejects a `completed` naming an unconfirmed file, so reaching
    COMPLETED at all proves the ordering held."""
    outcome, _store, _metrics, _job = await run_job(tmp_path, fake)
    assert outcome.state is JobState.COMPLETED
    for _job_id, page, kind, body in fake.page_reports:
        if kind == "completed":
            for artifact in body["artifacts"]:
                assert artifact["fileId"] in fake.confirmed, f"page {page}"


async def test_artifacts_are_real_and_well_formed(tmp_path: Path, fake: FakeBackend) -> None:
    """The bytes that reached storage are a real JPEG and real UTF-8 Arabic text."""
    await run_job(tmp_path, fake)
    images = [u["received"] for u in fake.uploads.values() if u["content_type"] == "image/jpeg"]
    texts = [u["received"] for u in fake.uploads.values() if u["content_type"] == "text/plain"]
    assert len(images) == 16
    assert len(texts) == 16
    assert all(payload.startswith(b"\xff\xd8\xff") for payload in images)  # JPEG SOI
    decoded = [payload.decode("utf-8") for payload in texts]
    assert all(text.endswith("\n") for text in decoded)
    # At least one page carries real diacritized Arabic.
    assert any("َ" in text or "ِ" in text for text in decoded)


async def test_ledger_and_artifact_records_match_what_was_uploaded(
    tmp_path: Path, fake: FakeBackend
) -> None:
    _outcome, store, _metrics, job = await run_job(tmp_path, fake)
    for page in range(1, 17):
        artifacts = await store.artifacts_for(job.job_id, page)
        assert set(artifacts) == {ArtifactType.IMAGE, ArtifactType.TEXT}
        for artifact in artifacts.values():
            assert artifact.backend_file_id in fake.confirmed
            assert artifact.size_in_bytes > 0


async def test_one_token_is_reused_across_the_whole_job(tmp_path: Path, fake: FakeBackend) -> None:
    """REQ-AUTH-02/03: a 300 s token is cached in memory, not refetched per call.

    A 16-page job makes well over 80 authenticated calls. Fetching a token for each would
    be both wrong and a small denial of service against Keycloak.
    """
    await run_job(tmp_path, fake)
    assert fake.tokens_issued == 1
    assert fake.ops().count("token") == 1


async def test_metrics_count_every_page(tmp_path: Path, fake: FakeBackend) -> None:
    _outcome, _store, metrics, _job = await run_job(tmp_path, fake)
    label = {"job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION.value}
    assert metrics.counter_value(PAGES_TOTAL, {**label, "outcome": "completed"}) == 16
    assert metrics.counter_value(PAGES_TOTAL, {**label, "outcome": "failed"}) == 0
