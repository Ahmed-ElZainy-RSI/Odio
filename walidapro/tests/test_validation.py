from __future__ import annotations

"""
These endpoints call paid external APIs (LightOn/Gemini) once past
request validation, so we can't exercise their success path without
real keys. What we CAN verify without any key: FastAPI/Pydantic reject
bad input before any external call is made.
"""


def test_ocr_verify_requires_file(client):
    response = client.post("/api/v1/ocr/verify")
    assert response.status_code == 422


def test_ocr_verify_rejects_non_pdf(client):
    response = client.post(
        "/api/v1/ocr/verify",
        files={"file": ("notes.txt", b"hi", "text/plain")},
    )
    assert response.status_code == 400


def test_final_review_rejects_empty_text(client):
    response = client.post("/api/v1/review/final-text", json={"text": ""})
    assert response.status_code == 422


def test_final_review_requires_text_field(client):
    response = client.post("/api/v1/review/final-text", json={})
    assert response.status_code == 422


def test_diacritization_requires_image_and_text(client):
    response = client.post("/api/v1/diacritization/page")
    assert response.status_code == 422


def test_diacritization_rejects_empty_corrected_text(client):
    response = client.post(
        "/api/v1/diacritization/page",
        files={"image": ("page.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        data={"corrected_text": "   "},
    )
    assert response.status_code == 400


def test_tts_generate_rejects_empty_text(client):
    response = client.post("/api/v1/tts/generate", json={"text": ""})
    assert response.status_code == 422


def test_tts_generate_rejects_bad_narrator(client):
    response = client.post(
        "/api/v1/tts/generate",
        json={"text": "hello", "narrator_type": "robot"},
    )
    assert response.status_code == 400


def test_tts_audio_download_unknown_id_404(client):
    response = client.get("/api/v1/tts/does-not-exist/audio")
    assert response.status_code == 404


def test_pipeline_create_job_requires_file(client):
    response = client.post("/api/v1/pipeline/jobs", data={"pages": "1"})
    assert response.status_code == 422


def test_pipeline_create_job_rejects_non_pdf(client):
    response = client.post(
        "/api/v1/pipeline/jobs",
        files={"file": ("notes.txt", b"hi", "text/plain")},
        data={"pages": "1"},
    )
    assert response.status_code == 400


def test_pipeline_create_job_rejects_bad_pages_spec(client, sample_pdf):
    # parse_pages() runs before any processing/threading is kicked off,
    # so this exercises real validation without touching external APIs.
    with sample_pdf.open("rb") as fh:
        response = client.post(
            "/api/v1/pipeline/jobs",
            files={"file": ("sample.pdf", fh, "application/pdf")},
            data={"pages": "not-a-valid-range"},
        )
    assert response.status_code == 400


def test_pipeline_get_unknown_job_404(client):
    response = client.get("/api/v1/pipeline/jobs/does-not-exist")
    assert response.status_code == 404
