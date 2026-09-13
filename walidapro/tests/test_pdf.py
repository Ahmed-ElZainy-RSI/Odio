from __future__ import annotations

"""
Fully local, no external API keys needed: PDF inspection/render are pure
PyMuPDF / pypdfium2 logic.
"""


def test_inspect_requires_pdf_extension(client):
    response = client.post(
        "/api/v1/pdf/inspect",
        files={"file": ("not_a_pdf.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_inspect_rejects_empty_pdf(client):
    response = client.post(
        "/api/v1/pdf/inspect",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_inspect_real_pdf_reports_ocr_required(client, sample_pdf):
    with sample_pdf.open("rb") as fh:
        response = client.post(
            "/api/v1/pdf/inspect",
            files={"file": ("sample.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pages"] == 1
    # Image-only PDF built from a PIL image has no embedded text layer.
    assert body["pdf_type"] == "OCR_REQUIRED"
    assert body["page_results"][0]["decision"] == "OCR_REQUIRED"


def test_render_page_returns_png(client, sample_pdf):
    with sample_pdf.open("rb") as fh:
        response = client.post(
            "/api/v1/pdf/render-page?page=1&dpi=100",
            files={"file": ("sample.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def test_render_page_out_of_range(client, sample_pdf):
    with sample_pdf.open("rb") as fh:
        response = client.post(
            "/api/v1/pdf/render-page?page=99&dpi=100",
            files={"file": ("sample.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 404


def test_render_page_rejects_bad_dpi(client, sample_pdf):
    with sample_pdf.open("rb") as fh:
        response = client.post(
            "/api/v1/pdf/render-page?page=1&dpi=5",
            files={"file": ("sample.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 400
