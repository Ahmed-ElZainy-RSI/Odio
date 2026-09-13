from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ocr_verifier.pipeline import OCRVerificationPipeline

from ..schemas import ErrorResponse, OCRVerifyResponse
from ..storage import OCR_DIR, new_id

router = APIRouter(prefix="/api/v1/ocr", tags=["2. OCR Verification"])


@router.post(
    "/verify",
    response_model=OCRVerifyResponse,
    status_code=200,
    responses={
        400: {"model": ErrorResponse, "description": "Missing/invalid PDF file."},
        502: {"model": ErrorResponse, "description": "OCR/verification pipeline failed (LightOn/Gemini/PaddleOCR)."},
    },
    summary="OCR a PDF and run context + visual verification (no diacritics/TTS)",
)
def verify(
    file: UploadFile = File(..., description="PDF file to OCR."),
    pages: str | None = Form(
        None,
        description='Page selector: "3" | "1-16" | "1,3,5" | "1-4,8-12". Omit for all pages.',
    ),
    dpi: int | None = Form(None, description="Render DPI. Defaults to OCR_DPI env / 220."),
) -> OCRVerifyResponse:
    """
    **Input:** multipart/form-data: `file` (PDF, required), `pages` (str,
    optional), `dpi` (int, optional).

    **Output:** the corrected text, the raw OCR text, and a stats summary
    (`total_words`, `suspicious_words`, `accepted`, `rejected`, `manual_review`).

    Runs: LightOn OCR -> suspicious-word context review -> bbox crop ->
    multi-variant visual re-OCR -> Gemini visual reviewer (only when needed).
    Any letter change without independent visual evidence is left as-is
    and flagged for manual review rather than guessed.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF (.pdf extension required).")

    data = file.file.read()

    if not data:
        raise HTTPException(400, "Uploaded PDF is empty.")

    request_id = new_id()
    output_dir = OCR_DIR / request_id
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = output_dir / "input.pdf"
    pdf_path.write_bytes(data)

    try:
        pipeline = OCRVerificationPipeline(output_dir)
        pipeline.run_pdf(pdf_path, pages, dpi)
    except Exception as exc:
        raise HTTPException(502, f"OCR verification pipeline failed: {exc}") from exc

    report_path = output_dir / "report.json"
    corrected_path = output_dir / "corrected_text.md"
    original_path = output_dir / "original_ocr.md"

    report = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.exists()
        else {}
    )

    return OCRVerifyResponse(
        request_id=request_id,
        report=report,
        original_text=(
            original_path.read_text(encoding="utf-8")
            if original_path.exists()
            else ""
        ),
        corrected_text=(
            corrected_path.read_text(encoding="utf-8")
            if corrected_path.exists()
            else ""
        ),
    )
