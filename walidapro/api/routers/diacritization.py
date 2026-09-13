from __future__ import annotations

import dataclasses
import io

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from ocr_verifier.diacritization.rebuild_text import rebuild_text
from ocr_verifier.diacritization.word_diacritics import WordDiacriticsExtractor

from ..schemas import DiacritizationResponse, ErrorResponse

router = APIRouter(prefix="/api/v1/diacritization", tags=["4. Diacritization"])


@router.post(
    "/page",
    response_model=DiacritizationResponse,
    status_code=200,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid image or missing text."},
        502: {"model": ErrorResponse, "description": "Diacritization pipeline failed (PaddleOCR/Gemini)."},
    },
    summary="Read visual diacritics from a page image and apply them to text",
)
def diacritize_page(
    image: UploadFile = File(..., description="Page image (PNG/JPG) matching corrected_text."),
    corrected_text: str = Form(..., description="Verified OCR text for this page (undiacritized or partially diacritized)."),
) -> DiacritizationResponse:
    """
    **Input:** multipart/form-data: `image` (page image) + `corrected_text`
    (form field, the OCR-verified text for that same page).

    **Output:** `final_text` with visual diacritics applied, plus a
    per-word breakdown (`status` = `OK` or `REVIEW`).

    Letters, punctuation, spacing and word order are never changed here —
    guaranteed by an internal preservation check. Only the diacritic marks
    are replaced, and only for words with a confident image-to-text
    alignment (>= 0.72) and unchanged letters after the visual read;
    everything else keeps its original diacritics and is marked `REVIEW`.
    """
    if not corrected_text.strip():
        raise HTTPException(400, "corrected_text must not be empty.")

    raw = image.file.read()

    if not raw:
        raise HTTPException(400, "Uploaded image is empty.")

    try:
        page_image = Image.open(io.BytesIO(raw)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(400, f"Invalid image file: {exc}") from exc

    extractor = WordDiacriticsExtractor()

    try:
        results = extractor.extract_page(
            page_image=page_image,
            corrected_text=corrected_text,
            debug_dir=None,
        )
        final_text = rebuild_text(corrected_text, results)
    except Exception as exc:
        raise HTTPException(502, f"Diacritization pipeline failed: {exc}") from exc

    review_words = [r for r in results if r.status != "OK"]

    return DiacritizationResponse(
        final_text=final_text,
        total_words=len(results),
        review_words=len(review_words),
        words=[dataclasses.asdict(r) for r in results],
    )
