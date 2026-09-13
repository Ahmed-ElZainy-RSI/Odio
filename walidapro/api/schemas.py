from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================
# COMMON
# ============================================================


class ErrorResponse(BaseModel):
    """
    Shape of every error response (FastAPI's default HTTPException body).
    """

    detail: str


# ============================================================
# PDF INSPECTION
# ============================================================


class PDFPageAnalysis(BaseModel):
    page: int
    text_chars: int
    visible_chars: int
    arabic_chars: int
    words: int
    images: int
    decision: str  # "TEXT_LAYER" | "OCR_REQUIRED"
    reason: str


class PDFInspectResponse(BaseModel):
    pdf: str
    pages: int
    text_layer_pages: int
    ocr_pages: int
    pdf_type: str  # "TEXT_LAYER" | "OCR_REQUIRED" | "MIXED_PDF"
    page_results: list[PDFPageAnalysis]


# ============================================================
# OCR VERIFICATION
# ============================================================


class OCRReportSummary(BaseModel):
    pages: int = 0
    total_words: int = 0
    suspicious_words: int = 0
    accepted: int = 0
    rejected: int = 0
    manual_review: int = 0


class OCRVerifyResponse(BaseModel):
    request_id: str
    report: OCRReportSummary
    original_text: str
    corrected_text: str


# ============================================================
# FINAL TEXT REVIEW
# ============================================================


class FinalReviewRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Raw OCR-corrected text to review.")


class FinalReviewResponse(BaseModel):
    original_text: str
    corrected_text: str
    changed: bool
    attempts: int
    review_required: bool
    rejection_reason: str | None
    changed_words: int
    changed_ratio: float


# ============================================================
# DIACRITIZATION
# ============================================================


class WordDiacriticsItem(BaseModel):
    index: int
    reference_word: str
    visual_word: str
    bbox: tuple[int, int, int, int]
    status: str  # "OK" | "REVIEW"
    crop_path: str | None = None
    paddle_text: str | None = None
    alignment_score: float | None = None


class DiacritizationResponse(BaseModel):
    final_text: str
    total_words: int
    review_words: int
    words: list[WordDiacriticsItem]


# ============================================================
# TTS
# ============================================================


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Arabic text to narrate.")
    narrator_type: str = Field("female", description="'female' (Leda) or 'male' (Orus).")
    voice: str | None = Field(None, description="Explicit Gemini voice name; overrides narrator_type default.")
    input_name: str | None = Field(None, description="Label used in saved files/reports.")


class TTSResponse(BaseModel):
    tts_id: str
    narrator_type: str
    voice: str
    model: str
    total_chunks: int
    successful_chunks: int
    failed_chunks: int
    total_characters: int
    total_duration_seconds: float
    audio_available: bool
    audio_url: str


# ============================================================
# FULL BOOK PIPELINE
# ============================================================


class PipelineJobCreateResponse(BaseModel):
    job_id: str
    pages: list[int]
    status: str  # "queued"
    status_url: str


class PipelineJobStatusResponse(BaseModel):
    job_id: str
    status: str  # "queued" | "running" | "done" | "error"
    pages_requested: list[int]
    dpi: int
    enable_final_context_review: bool
    error: str | None = None
    report_summary: dict | None = None


class PipelineJobListItem(BaseModel):
    job_id: str
    status: str
    pages_requested: list[int]


class PipelineJobTextResponse(BaseModel):
    text: str
