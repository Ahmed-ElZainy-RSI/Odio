from __future__ import annotations

import difflib

from .config import SETTINGS
from .models import Status
from .utils import normalize_ar


# ============================================================
# BASIC SIMILARITY
# ============================================================

def _similarity(
    a: str,
    b: str,
) -> float:
    """
    Arabic-normalized fuzzy similarity.
    """

    na = normalize_ar(
        a or ""
    )

    nb = normalize_ar(
        b or ""
    )

    if not na or not nb:
        return 0.0

    return difflib.SequenceMatcher(
        None,
        na,
        nb,
    ).ratio()


def visual_similarity_score(
    candidate: str,
    visual: str,
) -> float:
    

    return _similarity(
        candidate,
        visual,
    )


# ============================================================
# FINAL DECISION
# ============================================================

def decide(
    context_score: float,
    lighton_visual_score: float,
    validation_score: float,

    original: str,
    candidate: str,

    lighton_visual_reading: str,

    gemini_visual_reading: str | None = None,
    gemini_visual_confidence: float | None = None,
    gemini_supports_original: float | None = None,
    gemini_supports_candidate: float | None = None,
    gemini_visual_unclear: bool | None = None,
) -> tuple[float, Status]:
    

    # ========================================================
    # NORMALIZE INPUT SCORES
    # ========================================================

    context_score = max(
        0.0,
        min(
            1.0,
            float(context_score or 0.0),
        ),
    )

    lighton_visual_score = max(
        0.0,
        min(
            1.0,
            float(lighton_visual_score or 0.0),
        ),
    )

    validation_score = max(
        0.0,
        min(
            1.0,
            float(validation_score or 0.0),
        ),
    )

    gemini_visual_confidence = max(
        0.0,
        min(
            1.0,
            float(
                gemini_visual_confidence
                or 0.0
            ),
        ),
    )

    gemini_supports_original = max(
        0.0,
        min(
            1.0,
            float(
                gemini_supports_original
                or 0.0
            ),
        ),
    )

    gemini_supports_candidate = max(
        0.0,
        min(
            1.0,
            float(
                gemini_supports_candidate
                or 0.0
            ),
        ),
    )

    gemini_visual_unclear = bool(
        gemini_visual_unclear
    )

    # ========================================================
    # LIGHTON VISUAL EVIDENCE
    # ========================================================

    lighton_support_original = (
        _similarity(
            original,
            lighton_visual_reading,
        )
    )

    lighton_support_candidate = (
        _similarity(
            candidate,
            lighton_visual_reading,
        )
    )

    # ========================================================
    # GEMINI VISUAL EVIDENCE
    # ========================================================

    gemini_reading_support_original = 0.0
    gemini_reading_support_candidate = 0.0

    if gemini_visual_reading:

        gemini_reading_support_original = (
            _similarity(
                original,
                gemini_visual_reading,
            )
        )

        gemini_reading_support_candidate = (
            _similarity(
                candidate,
                gemini_visual_reading,
            )
        )

   

    if not gemini_visual_unclear:

        gemini_candidate_evidence = (
            gemini_supports_candidate
            * 0.55
            + gemini_reading_support_candidate
            * 0.45
        )

        gemini_original_evidence = (
            gemini_supports_original
            * 0.55
            + gemini_reading_support_original
            * 0.45
        )

        # ثقة Gemini تؤثر على قوة الدليل
        gemini_candidate_evidence *= (
            0.50
            + gemini_visual_confidence
            * 0.50
        )

        gemini_original_evidence *= (
            0.50
            + gemini_visual_confidence
            * 0.50
        )

    else:

        # Gemini قال UNCLEAR:
        # القراءة غير الحاسمة ليست دليلاً لصالح أي طرف.
        # لا نسمح لها أن تتجمع مع context/validation وتنتج ACCEPT.
        gemini_candidate_evidence = 0.0
        gemini_original_evidence = 0.0

    # ========================================================
    # VISUAL CONSENSUS
    # ========================================================

    candidate_visual_consensus = (
        lighton_support_candidate
        * 0.50
        + gemini_candidate_evidence
        * 0.50
    )

    original_visual_consensus = (
        lighton_support_original
        * 0.50
        + gemini_original_evidence
        * 0.50
    )

    # ========================================================
    # RULE 1
    # Both visual systems support ORIGINAL
    # ========================================================

    if (
        lighton_support_original >= 0.90
        and gemini_original_evidence >= 0.85
        and original_visual_consensus
        > candidate_visual_consensus
        and normalize_ar(original)
        != normalize_ar(candidate)
    ):
        """
        LightOn + Gemini Vision شايفين original.

        حتى لو Context Reviewer يقترح candidate،
        ممنوع ACCEPT.
        """

        final = (
            context_score * 0.25
            + candidate_visual_consensus * 0.45
            + validation_score * 0.30
        )

        return (
            min(
                final,
                SETTINGS.review_threshold
                + 0.05,
            ),
            "REVIEW",
        )

  

    visual_gap_against_candidate = (
        original_visual_consensus
        - candidate_visual_consensus
    )

    if (
        original_visual_consensus >= 0.80
        and visual_gap_against_candidate >= 0.12
    ):
        """
        الأدلة البصرية أقرب للأصل بفرق واضح.
        """

        final = (
            context_score * 0.25
            + candidate_visual_consensus * 0.45
            + validation_score * 0.30
        )

        return (
            min(
                final,
                SETTINGS.review_threshold
                + 0.05,
            ),
            "REVIEW",
        )

    
    if (
        lighton_support_candidate >= 0.90
        and gemini_candidate_evidence >= 0.90
        and context_score >= 0.90
        and validation_score >= 0.90
    ):
        """
        أقوى حالة ACCEPT.

        السياق + LightOn + Gemini Vision + Validation
        كلهم متفقين.
        """

        final = (
            context_score * 0.20
            + lighton_support_candidate * 0.25
            + gemini_candidate_evidence * 0.35
            + validation_score * 0.20
        )

        return (
            min(
                1.0,
                final,
            ),
            "ACCEPT",
        )

   
    if (
        gemini_candidate_evidence >= 0.92
        and gemini_visual_confidence >= 0.90
        and lighton_support_original < 0.90
        and context_score >= 0.92
        and validation_score >= 0.90
    ):
        """
        Gemini Vision واضح جدًا،
        والسياق قوي،
        وLightOn لا يؤيد original بقوة.
        """

        final = (
            context_score * 0.25
            + lighton_support_candidate * 0.15
            + gemini_candidate_evidence * 0.40
            + validation_score * 0.20
        )

        if (
            final
            >= SETTINGS.accept_threshold
        ):
            return (
                min(
                    1.0,
                    final,
                ),
                "ACCEPT",
            )

   

    # ========================================================
    # HARD RULE: UNCLEAR VISUAL CANNOT ACCEPT A LETTER CHANGE
    # ========================================================
    #
    # If candidate changes the normalized Arabic letters and Gemini
    # cannot read the crop clearly, keep the case for REVIEW/REJECT.
    # Context + validation are not visual ground truth.
    # ========================================================

    if (
        gemini_visual_unclear
        and normalize_ar(original) != normalize_ar(candidate)
    ):
        conservative_score = (
            context_score * 0.20
            + lighton_support_candidate * 0.35
            + validation_score * 0.20
        )

        conservative_score = max(
            0.0,
            min(
                conservative_score,
                SETTINGS.accept_threshold - 0.01,
            ),
        )

        if (
            lighton_support_candidate >= 0.92
            and context_score >= 0.90
            and validation_score >= 0.90
        ):
            return (
                conservative_score,
                "REVIEW",
            )

        return (
            min(
                conservative_score,
                SETTINGS.review_threshold - 0.01,
            ),
            "REJECT",
        )


    final = (
        context_score * 0.25
        + lighton_visual_score * 0.20
        + gemini_candidate_evidence * 0.35
        + validation_score * 0.20
    )

    final = max(
        0.0,
        min(
            1.0,
            final,
        ),
    )

 

    if (
        candidate_visual_consensus < 0.60
        and normalize_ar(original)
        != normalize_ar(candidate)
    ):

        if (
            final
            >= SETTINGS.review_threshold
        ):
            return (
                min(
                    final,
                    SETTINGS.accept_threshold
                    - 0.01,
                ),
                "REVIEW",
            )

        return (
            final,
            "REJECT",
        )

 

    # Generic ACCEPT is allowed only when the visual evidence itself
    # supports the candidate clearly. High context scores alone cannot
    # change Arabic letters.
    if (
        final >= SETTINGS.accept_threshold
        and normalize_ar(original) != normalize_ar(candidate)
    ):
        visual_gap_for_candidate = (
            candidate_visual_consensus
            - original_visual_consensus
        )

        if (
            candidate_visual_consensus < 0.82
            or visual_gap_for_candidate < 0.12
            or gemini_visual_unclear
        ):
            return (
                min(
                    final,
                    SETTINGS.accept_threshold - 0.01,
                ),
                "REVIEW",
            )

    if (
        final
        >= SETTINGS.accept_threshold
    ):
        return (
            final,
            "ACCEPT",
        )

    if (
        final
        >= SETTINGS.review_threshold
    ):
        return (
            final,
            "REVIEW",
        )

    return (
        final,
        "REJECT",
    )