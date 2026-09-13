from __future__ import annotations

"""
Tests for the POST /api/jobs contract (api/ai_contract/*, api/routers/ai_jobs.py).

The OCR pipeline and TTS pipeline are replaced with fakes here on purpose:
this file's job is to prove the CONTRACT plumbing is correct (auth, token
handling, idempotency, download, the 3-step upload cycle, and the status
callbacks) -- not to re-verify OCR/TTS quality, which needs real paid API
keys and belongs in a separate, opt-in integration test.
"""

import time
import wave
from pathlib import Path

import pytest
import requests_mock as requests_mock_lib


# ============================================================
# FAKES for the actual AI processing (isolates contract from OCR/TTS)
# ============================================================


class _FakeOCRPipeline:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_pdf(self, pdf_path, pages, dpi):
        (self.output_dir / "corrected_text.md").write_text(
            "نص تجريبي مصحح", encoding="utf-8"
        )


class _FakeTTSResult:
    def __init__(self, audio_path: Path):
        self.final_audio_path = str(audio_path)
        self.total_duration_seconds = 3.21
        self.failed_chunks = 0


class _FakeTTSPipeline:
    def __init__(self, output_dir, narrator_type=None, voice=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self, text, input_name):
        audio_path = self.output_dir / "final_audio.wav"
        with wave.open(str(audio_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * 100)
        return _FakeTTSResult(audio_path)


def _wait_for_status(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        if response.status_code == 200:
            last = response.json()
            if last.get("status") in {"done", "error"}:
                return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time; last state: {last}")


# ============================================================
# BASIC ENQUEUE / VALIDATION / IDEMPOTENCY (auth disabled)
# ============================================================


def test_enqueue_returns_202(client, ai_contract_env):
    response = client.post(
        "/api/jobs",
        json={
            "jobId": "job-basic-1",
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "file-1",
            "folderId": "folder-1",
            "pathPrefix": "books/1",
            "sourceDownloadUrl": f"{ai_contract_env.backend_base}/files/never-fetched",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["jobId"] == "job-basic-1"
    assert body["duplicate"] is False


def test_enqueue_same_jobid_twice_is_idempotent(client, ai_contract_env):
    payload = {
        "jobId": "job-dup-1",
        "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
        "fileId": "file-1",
        "sourceDownloadUrl": f"{ai_contract_env.backend_base}/files/never-fetched",
    }
    first = client.post("/api/jobs", json=payload)
    second = client.post("/api/jobs", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_enqueue_unknown_job_type_rejected(client, ai_contract_env):
    response = client.post(
        "/api/jobs",
        json={
            "jobId": "job-bad-type",
            "jobType": "NOT_A_REAL_TYPE",
            "fileId": "file-1",
            "sourceDownloadUrl": "http://x",
        },
    )
    assert response.status_code == 400


def test_audio_job_requires_page_number(client, ai_contract_env):
    response = client.post(
        "/api/jobs",
        json={
            "jobId": "job-audio-no-page",
            "jobType": "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
            "fileId": "file-1",
            "sourceDownloadUrl": "http://x",
        },
    )
    assert response.status_code == 400


def test_debug_get_unknown_job_404(client, ai_contract_env):
    response = client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404


# ============================================================
# AUTH (validated independently of ai_contract_env's auth_disabled=True)
# ============================================================


def test_missing_authorization_header_rejected(client, monkeypatch):
    from types import SimpleNamespace

    from api.ai_contract import auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "SETTINGS",
        SimpleNamespace(auth_disabled=False, jwt_issuer="http://fake/realm", jwt_audience=""),
    )

    response = client.post(
        "/api/jobs",
        json={
            "jobId": "job-no-auth",
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "f",
            "sourceDownloadUrl": "http://x",
        },
    )
    assert response.status_code == 401


def test_malformed_bearer_token_rejected(client, monkeypatch):
    from types import SimpleNamespace

    from api.ai_contract import auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "SETTINGS",
        SimpleNamespace(auth_disabled=False, jwt_issuer="http://fake/realm", jwt_audience=""),
    )

    response = client.post(
        "/api/jobs",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={
            "jobId": "job-bad-token",
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "f",
            "sourceDownloadUrl": "http://x",
        },
    )
    assert response.status_code == 401


# ============================================================
# FULL FLOW: PAGES_IMG_AND_TEXT_EXTRACTION
# ============================================================


def test_pages_extraction_job_full_flow(client, ai_contract_env, requests_mock, sample_pdf, monkeypatch):
    from api.ai_contract import processing

    monkeypatch.setattr(processing, "OCRVerificationPipeline", _FakeOCRPipeline)

    source_url = f"{ai_contract_env.backend_base}/files/source.pdf"
    requests_mock.get(source_url, content=sample_pdf.read_bytes())

    job_id = "job-extract-full"
    response = client.post(
        "/api/jobs",
        json={
            "jobId": job_id,
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "file-1",
            "folderId": "folder-1",
            "pathPrefix": "books/1",
            "sourceDownloadUrl": source_url,
        },
    )
    assert response.status_code == 202

    final = _wait_for_status(client, job_id)
    assert final["status"] == "done"

    kinds = [
        (r.method, r.url)
        for r in requests_mock.request_history
    ]

    assert (
        "POST",
        ai_contract_env.token_url,
    ) in kinds, "expected a client_credentials token request"

    metadata_calls = [
        r for r in requests_mock.request_history
        if r.url.endswith(f"/api/jobs/{job_id}/metadata")
    ]
    assert len(metadata_calls) == 1
    assert metadata_calls[0].json() == {"pagesCount": 1}

    completed_calls = [
        r for r in requests_mock.request_history
        if r.url.endswith(f"/api/jobs/{job_id}/1/completed")
    ]
    assert len(completed_calls) == 1
    artifacts = completed_calls[0].json()["artifacts"]
    types = {a["artifactType"] for a in artifacts}
    assert types == {"Image", "Text"}

    failed_calls = [
        r for r in requests_mock.request_history
        if "/failed" in r.url and f"/api/jobs/{job_id}/" in r.url
    ]
    assert not failed_calls


def test_pages_extraction_job_download_url_refresh_on_expiry(
    client, ai_contract_env, requests_mock, sample_pdf, monkeypatch
):
    """
    First GET on the given sourceDownloadUrl "expires" (403); the client
    must then call get-download-url and retry with the fresh URL.
    """
    from api.ai_contract import processing

    monkeypatch.setattr(processing, "OCRVerificationPipeline", _FakeOCRPipeline)

    expired_url = f"{ai_contract_env.backend_base}/files/expired.pdf"
    fresh_url = f"{ai_contract_env.backend_base}/files/fresh.pdf"

    requests_mock.get(expired_url, status_code=403)
    requests_mock.get(fresh_url, content=sample_pdf.read_bytes())
    requests_mock.get(
        requests_mock_lib.ANY,
        additional_matcher=lambda req: "/get-download-url" in req.url,
        json={"url": fresh_url},
    )

    job_id = "job-extract-refresh"
    response = client.post(
        "/api/jobs",
        json={
            "jobId": job_id,
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "file-expired",
            "sourceDownloadUrl": expired_url,
        },
    )
    assert response.status_code == 202

    final = _wait_for_status(client, job_id)
    assert final["status"] == "done"

    completed_calls = [
        r for r in requests_mock.request_history
        if r.url.endswith(f"/api/jobs/{job_id}/1/completed")
    ]
    assert len(completed_calls) == 1


def test_pages_extraction_job_page_error_reported_as_failed(
    client, ai_contract_env, requests_mock, sample_pdf, monkeypatch
):
    from api.ai_contract import processing

    class _BrokenPipeline:
        def __init__(self, output_dir):
            pass

        def run_pdf(self, pdf_path, pages, dpi):
            raise RuntimeError("boom: simulated OCR failure")

    monkeypatch.setattr(processing, "OCRVerificationPipeline", _BrokenPipeline)

    source_url = f"{ai_contract_env.backend_base}/files/source2.pdf"
    requests_mock.get(source_url, content=sample_pdf.read_bytes())

    job_id = "job-extract-page-fail"
    response = client.post(
        "/api/jobs",
        json={
            "jobId": job_id,
            "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
            "fileId": "file-1",
            "sourceDownloadUrl": source_url,
        },
    )
    assert response.status_code == 202

    final = _wait_for_status(client, job_id)
    assert final["status"] == "done"  # the job loop still completes every page

    failed_calls = [
        r for r in requests_mock.request_history
        if r.url.endswith(f"/api/jobs/{job_id}/1/failed")
    ]
    assert len(failed_calls) == 1
    assert "boom" in failed_calls[0].json()["reason"]


# ============================================================
# FULL FLOW: PAGE_AUDIO_AND_TEXT_TIMING_GENERATION
# ============================================================


def test_audio_timing_job_full_flow(client, ai_contract_env, requests_mock, monkeypatch):
    from api.ai_contract import processing

    monkeypatch.setattr(processing, "TTSPipeline", _FakeTTSPipeline)

    source_url = f"{ai_contract_env.backend_base}/files/free-text.txt"
    requests_mock.get(source_url, content="نص تجريبي للقراءة الصوتية".encode("utf-8"))

    job_id = "job-audio-full"
    response = client.post(
        "/api/jobs",
        json={
            "jobId": job_id,
            "jobType": "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
            "fileId": "text-file-1",
            "sourceDownloadUrl": source_url,
            "pageNumber": 3,
        },
    )
    assert response.status_code == 202

    final = _wait_for_status(client, job_id)
    assert final["status"] == "done"

    completed_calls = [
        r for r in requests_mock.request_history
        if r.url.endswith(f"/api/jobs/{job_id}/3/completed")
    ]
    assert len(completed_calls) == 1
    artifacts = completed_calls[0].json()["artifacts"]
    types = {a["artifactType"] for a in artifacts}
    assert types == {"Audio", "JSON"}
