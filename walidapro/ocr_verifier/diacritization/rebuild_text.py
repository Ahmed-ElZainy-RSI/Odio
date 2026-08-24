from __future__ import annotations

import re
from typing import Iterable

from .word_diacritics import WordDiacriticsResult


# ============================================================
# ARABIC CHARACTER DEFINITIONS
# ============================================================

ARABIC_LETTERS = (
    r"\u0621-\u063A"
    r"\u0641-\u064A"
    r"\u0671-\u06D3"
)

ARABIC_DIACRITICS = (
    r"\u0610-\u061A"
    r"\u064B-\u065F"
    r"\u0670"
    r"\u06D6-\u06ED"
)


# ============================================================
# REGEX
# ============================================================

ARABIC_LETTER_RE = re.compile(
    rf"[{ARABIC_LETTERS}]"
)

DIACRITICS_RE = re.compile(
    rf"[{ARABIC_DIACRITICS}]"
)

TOKEN_RE = re.compile(
    rf"([{ARABIC_LETTERS}{ARABIC_DIACRITICS}]+"
    rf"|[^{ARABIC_LETTERS}{ARABIC_DIACRITICS}]+)"
)


# ============================================================
# BASIC HELPERS
# ============================================================


def remove_diacritics(
    text: str,
) -> str:
    """
    إزالة التشكيل فقط.
    """

    return DIACRITICS_RE.sub(
        "",
        text or "",
    )


def contains_arabic(
    text: str,
) -> bool:
    """
    هل الجزء يحتوي على حرف عربي؟
    """

    if not text:
        return False

    return bool(
        ARABIC_LETTER_RE.search(
            text
        )
    )


def count_diacritics(
    text: str,
) -> int:
    """
    عدد علامات التشكيل الموجودة في الكلمة.
    """

    if not text:
        return 0

    return len(
        DIACRITICS_RE.findall(
            text
        )
    )


def has_meaningful_diacritics(
    text: str,
) -> bool:
    """
    هل الكلمة بالفعل تحتوي على تشكيل مفيد؟

    Helper إحصائي فقط.

    لا يُستخدم لتجاوز نتيجة Visual Diacritics،
    لأن وجود تشكيل داخل OCR لا يعني أنه مطابق للصورة الأصلية.
    """

    return (
        count_diacritics(
            text
        )
        >= 2
    )


# ============================================================
# TOKENIZATION
# ============================================================


def tokenize_preserving_layout(
    text: str,
) -> list[str]:
    """
    تقسيم النص مع الحفاظ الكامل على:

    - المسافات
    - الأسطر
    - علامات الترقيم
    - علامات الاقتباس
    - الأرقام
    """

    if not text:
        return []

    return TOKEN_RE.findall(
        text
    )


# ============================================================
# RESULT HELPERS
# ============================================================


def get_reference_plain(
    result: WordDiacriticsResult,
) -> str:

    return remove_diacritics(
        result.reference_word
        or ""
    )


def get_visual_plain(
    result: WordDiacriticsResult,
) -> str:

    return remove_diacritics(
        result.visual_word
        or ""
    )


# ============================================================
# REBUILD
# ============================================================


def rebuild_text(
    corrected_text: str,
    word_results: Iterable[
        WordDiacriticsResult
    ],
) -> str:
    """
    إعادة بناء النص النهائي.

    ------------------------------------------------------------
    أهم قاعدة
    ------------------------------------------------------------

    corrected_text هو Source of Truth.

    Visual Diacritics هو Source of Truth للتشكيل المرئي
    عندما تكون نتيجة الكلمة OK والحروف محفوظة.

    التشكيل الموجود داخل corrected_text قد يكون ناتج OCR،
    لذلك لا نعتمده لمجرد وجود علامتي تشكيل أو أكثر.

    إذا كانت النتيجة REVIEW أو غير موثوقة:
        نحافظ على الكلمة الداخلة كما هي ولا نخمن.

    ------------------------------------------------------------
    ممنوع تغيير
    ------------------------------------------------------------

    - الحروف
    - الكلمات
    - ترتيب الكلمات
    - علامات الترقيم
    - المسافات
    - الأسطر
    """

    if not corrected_text:
        return ""

    results = list(
        word_results
    )

    tokens = tokenize_preserving_layout(
        corrected_text
    )

    output: list[str] = []

    # ========================================================
    # COUNTERS
    # ========================================================

    result_index = 0

    arabic_token_count = 0

    applied_count = 0

    preserved_existing_count = 0

    unchanged_ok_count = 0

    review_count = 0

    mismatch_count = 0

    rejected_count = 0

    missing_count = 0

    # ========================================================
    # PROCESS TOKENS
    # ========================================================

    for token in tokens:

        # ----------------------------------------------------
        # NON-ARABIC
        # ----------------------------------------------------

        if not contains_arabic(
            token
        ):

            output.append(
                token
            )

            continue

        arabic_token_count += 1

        original_plain = remove_diacritics(
            token
        )

        # ----------------------------------------------------
        # NO RESULT
        # ----------------------------------------------------

        if result_index >= len(
            results
        ):

            output.append(
                token
            )

            missing_count += 1

            print(
                "[REBUILD MISSING] "
                f"word={original_plain}"
            )

            continue

        result = results[
            result_index
        ]

        result_index += 1

        reference_plain = (
            get_reference_plain(
                result
            )
        )

        visual_plain = (
            get_visual_plain(
                result
            )
        )

        # ====================================================
        # SAFETY 1
        # Reference must match corrected word
        # ====================================================

        if (
            original_plain
            != reference_plain
        ):

            output.append(
                token
            )

            mismatch_count += 1
            review_count += 1

            print(
                "[REBUILD WARNING] "
                f"index={result_index} | "
                f"Original={original_plain} | "
                f"Reference={reference_plain}"
            )

            continue

        # ====================================================
        # SAFETY 2
        # REVIEW => keep original
        # ====================================================

        if result.status != "OK":

            output.append(
                token
            )

            review_count += 1

            print(
                "[REBUILD REVIEW] "
                f"index={result_index} | "
                f"word={original_plain} | "
                f"status={result.status}"
            )

            continue

        # ====================================================
        # SAFETY 3
        # Visual letters must match original letters
        # ====================================================

        if (
            visual_plain
            != original_plain
        ):

            output.append(
                token
            )

            rejected_count += 1
            review_count += 1

            print(
                "[REBUILD REJECTED] "
                f"index={result_index} | "
                f"{original_plain} "
                f"-> "
                f"{result.visual_word}"
            )

            continue

        # ====================================================
        # SAFETY 4
        # VISUAL DIACRITICS ARE THE SOURCE OF TRUTH
        # ====================================================
        #
        # Important:
        # corrected_text may already contain OCR-produced diacritics.
        # We must NOT preserve them merely because there are 2+ marks.
        #
        # If:
        #   - visual result is OK
        #   - reference letters match
        #   - visual letters match
        #
        # then the diacritics read from the ORIGINAL PAGE image are used.
        #
        # Existing text diacritics are preserved only when the visual
        # result is REVIEW / rejected / missing (handled above).
        # ====================================================

        # ====================================================
        # NO VISUAL RESULT
        # ====================================================

        visual_word = (
            result.visual_word
            or token
        )

        if not visual_word:

            output.append(
                token
            )

            review_count += 1

            continue

        # ====================================================
        # VISUAL SAME AS ORIGINAL
        # ====================================================

        if visual_word == token:

            output.append(
                token
            )

            unchanged_ok_count += 1

            continue

        # ====================================================
        # APPLY VISUAL DIACRITICS
        # ====================================================

        output.append(
            visual_word
        )

        applied_count += 1

        print(
            "[REBUILD APPLY] "
            f"index={result_index} | "
            f"{token} "
            f"-> "
            f"{visual_word}"
        )

    # ========================================================
    # UNUSED RESULTS
    # ========================================================

    unused_results = max(
        0,
        len(results)
        - result_index,
    )

    # ========================================================
    # FINAL TEXT
    # ========================================================

    final_text = "".join(
        output
    )

    # ========================================================
    # PRESERVATION CHECK 1
    # ========================================================

    original_without_diacritics = (
        remove_diacritics(
            corrected_text
        )
    )

    final_without_diacritics = (
        remove_diacritics(
            final_text
        )
    )

    if (
        original_without_diacritics
        != final_without_diacritics
    ):

        raise RuntimeError(
            "REBUILD PRESERVATION FAILED: "
            "letters, punctuation, spaces, "
            "or layout changed."
        )

    # ========================================================
    # PRESERVATION CHECK 2
    # WORD SEQUENCE
    # ========================================================

    original_words = [
        remove_diacritics(
            token
        )
        for token in tokens
        if contains_arabic(
            token
        )
    ]

    final_tokens = (
        tokenize_preserving_layout(
            final_text
        )
    )

    final_words = [
        remove_diacritics(
            token
        )
        for token in final_tokens
        if contains_arabic(
            token
        )
    ]

    if (
        original_words
        != final_words
    ):

        raise RuntimeError(
            "REBUILD WORD PRESERVATION FAILED: "
            "Arabic word sequence changed."
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print(
        "=" * 60
    )

    print(
        "TEXT REBUILD SUMMARY"
    )

    print(
        "=" * 60
    )

    print(
        f"Arabic tokens         : "
        f"{arabic_token_count}"
    )

    print(
        f"Word results          : "
        f"{len(results)}"
    )

    print(
        f"Used results          : "
        f"{result_index}"
    )

    print(
        f"Visual applied        : "
        f"{applied_count}"
    )

    print(
        f"Existing preserved    : "
        f"{preserved_existing_count}"
    )

    print(
        f"Already unchanged     : "
        f"{unchanged_ok_count}"
    )

    print(
        f"Review/unchanged      : "
        f"{review_count}"
    )

    print(
        f"Reference mismatch    : "
        f"{mismatch_count}"
    )

    print(
        f"Visual rejected       : "
        f"{rejected_count}"
    )

    print(
        f"Missing results       : "
        f"{missing_count}"
    )

    print(
        f"Unused results        : "
        f"{unused_results}"
    )

    print()

    print(
        "[PRESERVATION] PASS"
    )

    print(
        "[WORD SEQUENCE] PASS"
    )

    anomaly_count = (
        review_count
        + mismatch_count
        + rejected_count
        + missing_count
        + unused_results
    )

    if anomaly_count > 0:

        print(
            "[REBUILD QUALITY] "
            f"Conservative fallback used "
            f"for {anomaly_count} event(s)."
        )

        print(
            "[REBUILD QUALITY] "
            "Letters/layout are preserved; "
            "uncertain diacritics were not guessed."
        )

    print(
        "=" * 60
    )

    return final_text