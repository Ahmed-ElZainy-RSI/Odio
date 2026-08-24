from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


Status = Literal[
    "ACCEPT",
    "REJECT",
    "REVIEW",
]


@dataclass
class WordBox:
    """
    Bounding Box ناتج من PaddleOCR.
    """

    text: str

    score: float

    bbox: tuple[
        int,
        int,
        int,
        int,
    ]


@dataclass
class SuspiciousWord:
    """
    يمثل كلمة مشكوك فيها خلال
    OCR Verification Pipeline.

    يحتوي على:

    1. Context Reviewer
    2. PaddleOCR BBox
    3. LightOn Visual Verification
    4. Gemini Visual Verification
    5. Final Decision
    """

    # ============================================================
    # BASIC INFORMATION
    # ============================================================

    page: int

    word: str

    sentence: str

    score: float

    reason: str

    # ============================================================
    # CONTEXT CANDIDATE
    # ============================================================

    candidate: str | None = None

    # ============================================================
    # PADDLE OCR / BOUNDING BOX
    # ============================================================

    bbox: tuple[
        int,
        int,
        int,
        int,
    ] | None = None

    bbox_text: str | None = None

    bbox_match_score: float | None = None

    # ============================================================
    # CROP FILES
    # ============================================================

    word_crop: str | None = None

    context_crop: str | None = None

    # ============================================================
    # LIGHTON VISUAL REVIEW
    # ============================================================

    visual_reading: str | None = None

    visual_score: float | None = None

    # ============================================================
    # GEMINI VISUAL REVIEW
    # ============================================================

    gemini_visual_reading: str | None = None

    gemini_visual_confidence: float | None = None

    gemini_supports_original: float | None = None

    gemini_supports_candidate: float | None = None

    gemini_visual_unclear: bool | None = None

    # ============================================================
    # CONTEXT REVIEW
    # ============================================================

    context_score: float | None = None

    # ============================================================
    # FINAL DECISION
    # ============================================================

    final_score: float | None = None

    status: Status = "REVIEW"

    # ============================================================
    # SERIALIZATION
    # ============================================================

    def to_dict(self) -> dict:
        """
        تحويل النتيجة إلى dict
        لكي يتم حفظها في corrections.jsonl.
        """

        return asdict(
            self
        )