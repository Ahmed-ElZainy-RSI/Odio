from __future__ import annotations

import io
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from ocr_verifier.pdf_render import render_pdf
from ocr_verifier.pdf_type_detector import inspect_pdf

from ..schemas import ErrorResponse, PDFInspectResponse
from ..storage import UPLOADS_DIR, new_id

router = APIRouter(prefix="/api/v1/pdf", tags=["1. PDF Inspection"])


def _save_upload(file: UploadFile) -> Path:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF (.pdf extension required).")

    data = file.file.read()

    if not data:
        raise HTTPException(400, "Uploaded PDF is empty.")

    dest = UPLOADS_DIR / f"{new_id()}.pdf"
    dest.write_bytes(data)
    return dest


@router.post(
    "/inspect",
    response_model=PDFInspectResponse,
    status_code=200,
    responses={
        400: {"model": ErrorResponse, "description": "Missing/invalid PDF file."},
        502: {"model": ErrorResponse, "description": "PDF could not be parsed."},
    },
    summary="Detect which pages need OCR vs. already have a text layer",
)
def inspect(file: UploadFile = File(..., description="PDF file to inspect.")) -> PDFInspectResponse:
    """
    **Input:** multipart/form-data with a `file` field (PDF).

    **Output:** per-page decision (`TEXT_LAYER` or `OCR_REQUIRED`) and an
    overall `pdf_type` (`TEXT_LAYER` | `OCR_REQUIRED` | `MIXED_PDF`).

    Pure local analysis (PyMuPDF) — no external API calls.
    """
    pdf_path = _save_upload(file)

    try:
        result = inspect_pdf(str(pdf_path))
    except Exception as exc:
        raise HTTPException(502, f"Failed to inspect PDF: {exc}") from exc
    finally:
        pdf_path.unlink(missing_ok=True)

    return PDFInspectResponse(**result)


@router.post(
    "/render-page",
    status_code=200,
    responses={
        400: {"model": ErrorResponse, "description": "Missing/invalid PDF file or page number."},
        404: {"model": ErrorResponse, "description": "Requested page does not exist."},
        502: {"model": ErrorResponse, "description": "PDF could not be rendered."},
        200: {"content": {"image/png": {}}, "description": "PNG image of the requested page."},
    },
    summary="Render one PDF page to a PNG image",
)
def render_page(
    file: UploadFile = File(..., description="PDF file to render."),
    page: int = 1,
    dpi: int = 220,
):
    """
    **Input:** multipart/form-data with `file` (PDF), plus query/form params
    `page` (1-based, default 1) and `dpi` (default 220).

    **Output:** raw `image/png` bytes of the rendered page.
    """
    if page < 1:
        raise HTTPException(400, "page must be >= 1.")

    if dpi < 36 or dpi > 1200:
        raise HTTPException(400, "dpi must be between 36 and 1200.")

    pdf_path = _save_upload(file)

    try:
        try:
            rendered = render_pdf(pdf_path, dpi, str(page))
        except Exception as exc:
            raise HTTPException(502, f"Failed to render PDF: {exc}") from exc

        if not rendered:
            raise HTTPException(404, f"Page {page} could not be rendered (out of range?).")

        _, image = rendered[0]

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        return Response(content=buffer.getvalue(), media_type="image/png")
    finally:
        pdf_path.unlink(missing_ok=True)
