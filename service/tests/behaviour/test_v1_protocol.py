"""The V1 protocol, stated as behaviour.

Each test reads as a scenario from the integration spec rather than as a unit of code, and
each docstring quotes the sentence it enforces. The point is that a reviewer from the
Backend team should be able to read this file and check we understood their contract --
without reading any implementation.

Everything runs against the contract fake. Zero network, zero paid calls.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai_engine.adapters.ai.tts import SyntheticTts
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.domain.enums import JobState, JobType
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings
from tests.e2e.test_job_a_failure_paths import small_pdf_bytes
from tests.e2e.test_job_b_end_to_end import (
    ARABIC_TEXT,
    make_audio_job,
    perfect_transcriber,
)
from tests.fakes.backend import FakeBackend

PAGES = 4


def extraction_job(file_id: str = "book-1") -> Job:
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


async def given_a_job_is_run(
    tmp_path: Path, fake: FakeBackend, job: Job, *, store: InMemoryJobStore | None = None
):
    store = store or InMemoryJobStore()
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
            tts=SyntheticTts(),
            transcriber=perfect_transcriber(ARABIC_TEXT),
        )
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet()
    return outcome, store


@pytest.fixture
def pdf_backend() -> FakeBackend:
    return FakeBackend(source_bytes=small_pdf_bytes(PAGES))


@pytest.fixture
def text_backend() -> FakeBackend:
    return FakeBackend(sources={"page-text": ARABIC_TEXT.encode("utf-8")})


# ── Enqueue ─────────────────────────────────────────────────────────────────────


class TestWhenTheBackendEnqueuesAJob:
    """*"make post request to {AI_Engine_BASE_URL}/api/jobs ... return 202 accepted"*"""

    def test_a_new_job_is_accepted_with_202(self, client) -> None:
        from tests.conftest import job_a_body

        assert client.post("/api/jobs", json=job_a_body(uuid4())).status_code == 202

    def test_no_ai_work_happens_in_the_request(self, client) -> None:
        """The response must not wait for a book to be processed."""
        from tests.conftest import job_a_body

        response = client.post("/api/jobs", json=job_a_body(uuid4()))
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"

    def test_the_same_job_id_twice_is_still_202(self, client) -> None:
        """*"if already enqueued return 202 accepted"*"""
        from tests.conftest import job_a_body

        body = job_a_body(uuid4())
        assert client.post("/api/jobs", json=body).status_code == 202
        assert client.post("/api/jobs", json=body).status_code == 202

    def test_the_same_job_id_is_only_executed_once(self, client, store) -> None:
        """*"make jobs idempotent to guarantee not enqueue it multiple time"*"""
        from tests.conftest import job_a_body

        body = job_a_body(uuid4())
        for _ in range(4):
            client.post("/api/jobs", json=body)
        assert len(store._jobs) == 1


# ── Extraction: the page-count gate ─────────────────────────────────────────────


class TestWhenExtractingABook:
    """*"backend wants to know how many pages in pdf so ai engine should give the number
    of pages in the pdf before start text and image extraction"*"""

    async def test_the_page_count_is_reported_first(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        job = extraction_job()
        await given_a_job_is_run(tmp_path, pdf_backend, job)
        ops = pdf_backend.ops()
        assert ops.index("metadata") < ops.index("initialize_upload")
        assert pdf_backend.metadata[str(job.job_id)] == PAGES

    async def test_no_page_work_starts_if_the_count_cannot_be_reported(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """Page callbacks for a job whose page count the Backend does not know would be
        incoherent, so the job fails instead."""
        pdf_backend.faults.callback_status = 503
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert outcome.state is JobState.FAILED
        assert "initialize_upload" not in pdf_backend.ops()
        assert pdf_backend.page_reports == []

    async def test_each_page_yields_one_image_and_one_text_file(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"one page should result one image file and one text file"*"""
        await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        types = [c.body["contentType"] for c in pdf_backend.calls if c.op == "initialize_upload"]
        assert types.count("image/jpeg") == PAGES
        assert types.count("text/plain") == PAGES

    async def test_the_upload_cycle_is_image_then_text(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"call upload cycle per (image) then (text)"*"""
        await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        types = [c.body["contentType"] for c in pdf_backend.calls if c.op == "initialize_upload"]
        assert types[:2] == ["image/jpeg", "text/plain"]

    async def test_each_upload_is_three_steps_in_order(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """initialize-upload -> PUT -> confirm-upload."""
        await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        upload_ops = [
            c.op
            for c in pdf_backend.calls
            if c.op in {"initialize_upload", "put_upload", "confirm_upload"}
        ]
        assert upload_ops[:3] == ["initialize_upload", "put_upload", "confirm_upload"]

    async def test_completed_is_sent_only_after_both_files_succeed(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"And after both file success"* -- the fake returns 409 otherwise."""
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert outcome.state is JobState.COMPLETED
        for _job, _page, kind, body in pdf_backend.page_reports:
            if kind == "completed":
                for artifact in body["artifacts"]:
                    assert artifact["fileId"] in pdf_backend.confirmed


# ── Page completion totality ────────────────────────────────────────────────────


class TestWhenSomePagesFail:
    """*"the backend will consider that you finish your process after you completed the 30
    pages whether they fail or succeed, if 29, no, the backend will wait for number 30
    page"*"""

    async def test_every_page_reports_even_when_one_fails(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        from tests.e2e.test_job_a_failure_paths import FlakyRenderer

        store = InMemoryJobStore()
        job = extraction_job()
        await store.accept(job)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=pdf_backend.app), base_url=BACKEND
        ) as client:
            worker = build_worker(
                settings=make_settings(tmp_path),
                store=store,
                metrics=Metrics(),
                http_client=client,
            )
            worker.extraction._renderer = FlakyRenderer(failing_pages={2})
            worker.extraction._pages._renderer = FlakyRenderer(failing_pages={2})
            outcome = await worker.run_job(job)
            await worker.drain_until_quiet()

        reports = pdf_backend.terminal_reports()
        assert sorted(reports) == list(range(1, PAGES + 1))
        assert reports[2] == "failed"
        assert outcome.state is JobState.COMPLETED

    async def test_a_failed_page_carries_a_reason(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"you want to tell backend you failed and here are the reason"*"""
        from tests.e2e.test_job_a_failure_paths import FlakyRenderer

        store = InMemoryJobStore()
        job = extraction_job()
        await store.accept(job)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=pdf_backend.app), base_url=BACKEND
        ) as client:
            worker = build_worker(
                settings=make_settings(tmp_path),
                store=store,
                metrics=Metrics(),
                http_client=client,
            )
            worker.extraction._pages._renderer = FlakyRenderer(failing_pages={1})
            worker.extraction._renderer = FlakyRenderer(failing_pages=set())
            await worker.run_job(job)
            await worker.drain_until_quiet()

        reason = next(b["reason"] for _j, _p, k, b in pdf_backend.page_reports if k == "failed")
        assert reason
        assert len(reason) <= 512


class TestWhenTheSourceCannotBeUsed:
    """*"at some time you want to mark whole job as failed i.e you cant parse or get the
    pdf"*"""

    async def test_an_unparseable_pdf_fails_the_whole_job(self, tmp_path: Path) -> None:
        fake = FakeBackend(source_bytes=b"PK\x03\x04 a zip")
        outcome, _store = await given_a_job_is_run(tmp_path, fake, extraction_job())
        assert outcome.state is JobState.FAILED
        assert len(fake.job_failures) == 1

    async def test_no_page_callbacks_are_sent_for_a_failed_job(self, tmp_path: Path) -> None:
        fake = FakeBackend(source_bytes=b"not a pdf at all")
        await given_a_job_is_run(tmp_path, fake, extraction_job())
        assert fake.page_reports == []


class TestWhenTheDownloadUrlHasExpired:
    """*"at some times download pdf via the link could be expired, so that the ai engine
    should request a new url from our backend to get a fresh url for download"*"""

    async def test_a_fresh_url_is_requested_and_the_job_proceeds(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        pdf_backend.faults.source_expired_times = 1
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert "get_download_url" in pdf_backend.ops()
        assert outcome.state is JobState.COMPLETED


# ── Audio and timing ────────────────────────────────────────────────────────────


class TestWhenGeneratingAudio:
    """*"AI engine read the free text file and make audio generation and want to send
    audio and json to Backend"*"""

    async def test_one_audio_file_and_one_json_file_are_produced(
        self, tmp_path: Path, text_backend: FakeBackend
    ) -> None:
        """*"one page should result one audio file and one json file"*"""
        await given_a_job_is_run(tmp_path, text_backend, make_audio_job())
        types = [c.body["contentType"] for c in text_backend.calls if c.op == "initialize_upload"]
        assert types == ["audio/wav", "application/json"]

    async def test_the_upload_cycle_is_audio_then_json(
        self, tmp_path: Path, text_backend: FakeBackend
    ) -> None:
        """*"call upload cycle per (audio) then (json)"*"""
        await given_a_job_is_run(tmp_path, text_backend, make_audio_job())
        assert [t for t, _ in text_backend.artifacts_for(7)] == ["Audio", "JSON"]

    async def test_the_given_page_number_is_the_one_reported(
        self, tmp_path: Path, text_backend: FakeBackend
    ) -> None:
        """*"PATCH .../{Given pageNumber}/completed"* -- Job B reports the page it was
        given, which may be page 7 of a book."""
        await given_a_job_is_run(tmp_path, text_backend, make_audio_job(page=7))
        assert list(text_backend.terminal_reports()) == [7]

    async def test_no_page_count_is_reported_for_a_single_page_job(
        self, tmp_path: Path, text_backend: FakeBackend
    ) -> None:
        await given_a_job_is_run(tmp_path, text_backend, make_audio_job())
        assert "metadata" not in text_backend.ops()


# ── Auth ────────────────────────────────────────────────────────────────────────


class TestWhenCallingTheBackend:
    """*"Load/get {backend-bearer-token} to be trusted to call backend"*"""

    async def test_every_backend_call_carries_a_bearer_token(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """The fake returns 401 without one, so completing proves it was always present."""
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert outcome.state is JobState.COMPLETED

    async def test_the_token_is_fetched_once_and_reused(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"it is better not to call the token api before each request"*"""
        await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert pdf_backend.tokens_issued == 1

    async def test_a_401_causes_a_refresh_and_a_retry(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """*"401 means unauthorized token or expired"*"""
        pdf_backend.faults.confirm_status = 401
        pdf_backend.faults.confirm_failures = 1
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert pdf_backend.tokens_issued >= 2
        assert outcome.state is JobState.COMPLETED

    async def test_the_presigned_put_never_carries_our_backend_token(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """Sending it would hand our Backend credential to the storage provider. The fake
        returns 400 if it sees one, so completing proves we did not."""
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert outcome.state is JobState.COMPLETED
        puts = [c for c in pdf_backend.calls if c.op == "put_upload"]
        assert puts
        assert all("authorization" not in c.headers for c in puts)


# ── Content types ───────────────────────────────────────────────────────────────


class TestArtifactContentTypes:
    """*"contentType": "<image/jpeg> or <application/json> or <audio/wav> or <text/plain>"*"""

    async def test_only_the_four_permitted_types_are_sent(
        self, tmp_path: Path, pdf_backend: FakeBackend, text_backend: FakeBackend
    ) -> None:
        await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        await given_a_job_is_run(tmp_path, text_backend, make_audio_job())
        sent = {
            c.body["contentType"]
            for fake in (pdf_backend, text_backend)
            for c in fake.calls
            if c.op == "initialize_upload"
        }
        assert sent == {"image/jpeg", "text/plain", "audio/wav", "application/json"}

    async def test_the_declared_size_matches_the_bytes_sent(
        self, tmp_path: Path, pdf_backend: FakeBackend
    ) -> None:
        """The fake returns 400 on a Content-Length mismatch, because a presigned PUT is
        signed over its headers."""
        outcome, _store = await given_a_job_is_run(tmp_path, pdf_backend, extraction_job())
        assert outcome.state is JobState.COMPLETED
