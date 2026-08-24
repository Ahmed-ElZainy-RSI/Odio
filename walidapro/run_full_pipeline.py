from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from ocr_verifier.pdf_render import render_pdf

from ocr_verifier.final_context_reviewer import (
    FinalContextReviewer,
)

from ocr_verifier.diacritization.word_diacritics import (
    WordDiacriticsExtractor,
)

from ocr_verifier.diacritization.rebuild_text import (
    rebuild_text,
)

from ocr_verifier.tts.pipeline import (
    TTSPipeline,
)


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = Path(
    __file__
).resolve().parent


# ============================================================
# HELPERS
# ============================================================


def print_header(
    title: str,
) -> None:

    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def ensure_exists(
    path: Path,
    description: str,
) -> None:

    if not path.exists():

        raise FileNotFoundError(
            f"{description} not found: {path}"
        )


def read_text_file(
    path: Path,
) -> str:

    ensure_exists(
        path,
        "Text file",
    )

    return path.read_text(
        encoding="utf-8"
    ).strip()


def save_json(
    path: Path,
    data: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_json_if_exists(
    path: Path,
) -> dict:

    if not path.exists():

        return {}

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {}


# ============================================================
# PAGE RANGE
# ============================================================


def parse_pages(
    value: str,
) -> list[int]:
    """
    Supported:

        3
        1-16
        1,3,5
        1-4,7,10-12
    """

    value = value.strip()

    if not value:

        raise ValueError(
            "--pages cannot be empty."
        )

    pages: set[int] = set()

    parts = [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]

    for part in parts:

        if "-" in part:

            start_raw, end_raw = (
                part.split(
                    "-",
                    1,
                )
            )

            if (
                not start_raw.isdigit()
                or not end_raw.isdigit()
            ):

                raise ValueError(
                    f"Invalid page range: {part}"
                )

            start = int(
                start_raw
            )

            end = int(
                end_raw
            )

            if start <= 0 or end <= 0:

                raise ValueError(
                    "Page numbers must be >= 1."
                )

            if end < start:

                raise ValueError(
                    f"Invalid range: {part}"
                )

            pages.update(
                range(
                    start,
                    end + 1,
                )
            )

        else:

            if not part.isdigit():

                raise ValueError(
                    f"Invalid page: {part}"
                )

            page = int(
                part
            )

            if page <= 0:

                raise ValueError(
                    "Page numbers must be >= 1."
                )

            pages.add(
                page
            )

    if not pages:

        raise ValueError(
            "No valid pages specified."
        )

    return sorted(
        pages
    )


# ============================================================
# ARABIC WORD HELPERS
# ============================================================


ARABIC_DIACRITICS = re.compile(
    r"[\u0610-\u061A"
    r"\u064B-\u065F"
    r"\u0670"
    r"\u06D6-\u06ED]"
)

ARABIC_WORD = re.compile(
    r"[\u0621-\u063A"
    r"\u0641-\u064A"
    r"\u066E-\u06D3]+"
)


def strip_diacritics(
    text: str,
) -> str:

    return ARABIC_DIACRITICS.sub(
        "",
        text,
    )


def normalize_arabic_word(
    word: str,
) -> str:

    word = strip_diacritics(
        word
    )

    word = unicodedata.normalize(
        "NFKC",
        word,
    )

    return word.strip()


def extract_arabic_words(
    text: str,
) -> list[str]:

    return [
        normalize_arabic_word(
            word
        )
        for word in ARABIC_WORD.findall(
            text
        )
    ]


def count_changed_words(
    before: str,
    after: str,
) -> int:
    """
    تقدير عدد الكلمات التي غيرها
    Final Context Reviewer.

    ليست CER/WER.
    دي Metric للـDashboard فقط:
    final_review_changed_words.
    """

    before_words = extract_arabic_words(
        before
    )

    after_words = extract_arabic_words(
        after
    )

    matcher = SequenceMatcher(
        None,
        before_words,
        after_words,
    )

    changed = 0

    for (
        tag,
        i1,
        i2,
        j1,
        j2,
    ) in matcher.get_opcodes():

        if tag == "equal":
            continue

        if tag == "replace":

            changed += max(
                i2 - i1,
                j2 - j1,
            )

        elif tag == "delete":

            changed += (
                i2 - i1
            )

        elif tag == "insert":

            changed += (
                j2 - j1
            )

    return changed


# ============================================================
# STEP 0
# RENDER + SAVE ORIGINAL PAGE
# ============================================================


def render_and_save_original_page(
    input_file: Path,
    page_number: int,
    dpi: int,
    page_dir: Path,
) -> tuple[
    Image.Image,
    Path,
]:
    """
    Render original PDF page and save:

        page_xxx/original_page.png
    """

    print_header(
        f"PAGE {page_number} - ORIGINAL PAGE"
    )

    rendered_pages = render_pdf(
        path=input_file,
        dpi=dpi,
        pages=str(
            page_number
        ),
    )

    if not rendered_pages:

        raise RuntimeError(
            f"Page {page_number} "
            "could not be rendered."
        )

    page_image: Image.Image | None = None

    for (
        rendered_page_number,
        rendered_image,
    ) in rendered_pages:

        if (
            rendered_page_number
            == page_number
        ):

            page_image = (
                rendered_image
            )

            break

    if page_image is None:

        raise RuntimeError(
            f"Rendered page {page_number} "
            "not found."
        )

    page_image = (
        page_image
        .convert(
            "RGB"
        )
        .copy()
    )

    original_page_path = (
        page_dir
        / "original_page.png"
    )

    page_image.save(
        original_page_path,
        format="PNG",
    )

    print(
        f"[PAGE] {page_number}"
    )

    print(
        f"[SIZE] "
        f"{page_image.width}"
        f"x"
        f"{page_image.height}"
    )

    print(
        f"[ORIGINAL IMAGE] "
        f"{original_page_path}"
    )

    return (
        page_image,
        original_page_path,
    )


# ============================================================
# STEP 1
# OCR + CURRENT REVIEWER
# ============================================================


def run_ocr_pipeline(
    input_file: Path,
    output_dir: Path,
    page_number: int,
    dpi: int,
) -> dict:
    """
    run.py:

        LightOn OCR
            ↓
        Current Context Reviewer
            ↓
        Visual Verification
            ↓
        corrected_text.md

    output_dir هنا خاص بالصفحة.
    """

    print_header(
        f"PAGE {page_number} - STEP 1 - OCR + INITIAL REVIEW"
    )

    run_py = (
        PROJECT_ROOT
        / "run.py"
    )

    ensure_exists(
        run_py,
        "run.py",
    )

    command = [
        sys.executable,
        str(run_py),
        str(input_file),
        "-o",
        str(output_dir),
        "--dpi",
        str(dpi),
        "--pages",
        str(page_number),
    ]

    print(
        "[RUN]",
        " ".join(
            command
        ),
    )

    result = subprocess.run(
        command,
        cwd=str(
            PROJECT_ROOT
        ),
    )

    if result.returncode != 0:

        raise RuntimeError(
            f"OCR pipeline failed "
            f"for page {page_number}."
        )

    # --------------------------------------------------------
    # FILES
    # --------------------------------------------------------

    original_ocr = (
        output_dir
        / "original_ocr.md"
    )

    corrected_path = (
        output_dir
        / "corrected_text.md"
    )

    verification_report = (
        output_dir
        / "verification_report.md"
    )

    report_json = (
        output_dir
        / "report.json"
    )

    ensure_exists(
        corrected_path,
        "Corrected OCR text",
    )

    stats = load_json_if_exists(
        report_json
    )

    print()
    print(
        f"[OCR] Corrected text: "
        f"{corrected_path}"
    )

    if stats:

        print(
            f"[OCR STATS] "
            f"Words={stats.get('total_words', 0)} | "
            f"Suspicious={stats.get('suspicious_words', 0)} | "
            f"Accepted={stats.get('accepted', 0)} | "
            f"Rejected={stats.get('rejected', 0)} | "
            f"Manual={stats.get('manual_review', 0)}"
        )

    return {
        "corrected_path":
            corrected_path,

        "original_ocr_path":
            original_ocr
            if original_ocr.exists()
            else None,

        "verification_report_path":
            verification_report
            if verification_report.exists()
            else None,

        "report_json_path":
            report_json
            if report_json.exists()
            else None,

        "stats":
            stats,
    }


# ============================================================
# STEP 2
# FINAL GEMINI PRO REVIEW
# ============================================================


def run_final_context_review(
    corrected_text: str,
    output_dir: Path,
    page_number: int,
) -> dict:
    """
    Gemini 3.1 Pro final context review.

    هذه المرحلة Quality Gate قبل التشكيل والصوت.
    """

    print_header(
        f"PAGE {page_number} - STEP 2 - FINAL CONTEXT REVIEW"
    )

    reviewer = (
        FinalContextReviewer()
    )

    result = reviewer.review(
        corrected_text
    )

    if not result.corrected_text.strip():

        raise RuntimeError(
            "Final Context Reviewer "
            "returned empty text."
        )

    final_review_dir = (
        output_dir
        / "final_review"
    )

    final_review_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    before_path = (
        final_review_dir
        / "before_final_review.txt"
    )

    final_path = (
        final_review_dir
        / "final_corrected_text.txt"
    )

    before_path.write_text(
        result.original_text,
        encoding="utf-8",
    )

    final_path.write_text(
        result.corrected_text,
        encoding="utf-8",
    )

    changed_words = (
        count_changed_words(
            result.original_text,
            result.corrected_text,
        )
    )

    final_review_report = {
        "changed":
            result.changed,

        "attempts":
            result.attempts,

        "changed_words":
            changed_words,

        "changed_ratio":
            float(
                getattr(
                    result,
                    "changed_ratio",
                    0.0,
                )
                or 0.0
            ),

        "review_required":
            bool(
                getattr(
                    result,
                    "review_required",
                    False,
                )
            ),

        "rejection_reason":
            getattr(
                result,
                "rejection_reason",
                None,
            ),

        "before_word_count":
            len(
                extract_arabic_words(
                    result.original_text
                )
            ),

        "after_word_count":
            len(
                extract_arabic_words(
                    result.corrected_text
                )
            ),
    }

    final_report_path = (
        final_review_dir
        / "report.json"
    )

    save_json(
        final_report_path,
        final_review_report,
    )

    print()
    print(
        f"[FINAL REVIEW] "
        f"Changed: {result.changed}"
    )

    print(
        f"[FINAL REVIEW] "
        f"Changed words: {changed_words}"
    )

    print(
        f"[FINAL REVIEW] "
        f"Attempts: {result.attempts}"
    )

    print(
        f"[FINAL REVIEW] "
        f"Final: {final_path}"
    )

    return {
        "path":
            final_path,

        "text":
            result.corrected_text,

        "changed":
            result.changed,

        "changed_words":
            changed_words,

        "attempts":
            result.attempts,

        "review_required":
            bool(
                getattr(
                    result,
                    "review_required",
                    False,
                )
            ),

        "rejection_reason":
            getattr(
                result,
                "rejection_reason",
                None,
            ),

        "changed_ratio":
            float(
                getattr(
                    result,
                    "changed_ratio",
                    0.0,
                )
                or 0.0
            ),

        "report_path":
            final_report_path,
    }


# ============================================================
# STEP 3
# VISUAL DIACRITICS
# ============================================================


def run_diacritization(
    corrected_text: str,
    page_image: Image.Image,
    output_dir: Path,
    page_number: int,
) -> dict:

    print_header(
        f"PAGE {page_number} - STEP 3 - VISUAL DIACRITICS"
    )

    diacritics_dir = (
        output_dir
        / "diacritization"
    )

    word_crops_dir = (
        diacritics_dir
        / "word_crops"
    )

    diacritics_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    word_crops_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    extractor = (
        WordDiacriticsExtractor()
    )

    results = (
        extractor.extract_page(
            page_image=page_image,
            corrected_text=corrected_text,
            debug_dir=word_crops_dir,
        )
    )

    if not results:

        raise RuntimeError(
            "Word diacritics returned "
            "no results."
        )

    # --------------------------------------------------------
    # REBUILD
    # --------------------------------------------------------

    final_text = rebuild_text(
        corrected_text=corrected_text,
        word_results=results,
    )

    if not final_text.strip():

        raise RuntimeError(
            "Rebuilt diacritized text "
            "is empty."
        )

    final_path = (
        diacritics_dir
        / "final_diacritized_text.txt"
    )

    final_path.write_text(
        final_text,
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # REVIEW WORDS
    # --------------------------------------------------------

    review_words = [
        item
        for item in results
        if item.status != "OK"
    ]

    review_path = (
        diacritics_dir
        / "review_words.txt"
    )

    review_lines: list[str] = []

    for item in review_words:

        review_lines.append(
            (
                f"{item.index}\t"
                f"{item.reference_word}\t"
                f"{item.visual_word}\t"
                f"{item.status}\t"
                f"alignment="
                f"{item.alignment_score}"
            )
        )

    review_path.write_text(
        "\n".join(
            review_lines
        ),
        encoding="utf-8",
    )

    diacritics_report = {
        "total_words":
            len(results),

        "review_words":
            len(review_words),

        "ok_words":
            len(results)
            - len(review_words),

        "review_rate":
            (
                len(review_words)
                / len(results)
                * 100
            )
            if results
            else 0.0,
    }

    report_path = (
        diacritics_dir
        / "report.json"
    )

    save_json(
        report_path,
        diacritics_report,
    )

    print()
    print(
        f"[DIACRITICS] "
        f"Total words: "
        f"{len(results)}"
    )

    print(
        f"[DIACRITICS] "
        f"Review words: "
        f"{len(review_words)}"
    )

    print(
        f"[DIACRITICS] "
        f"Final: "
        f"{final_path}"
    )

    return {
        "path":
            final_path,

        "text":
            final_text,

        "total_words":
            len(results),

        "review_words":
            len(review_words),

        "review_path":
            review_path,

        "report_path":
            report_path,
    }


# ============================================================
# STEP 4
# LEDA + ORUS
# ============================================================


def run_dual_tts(
    text: str,
    output_dir: Path,
    input_name: str,
    page_number: int,
) -> dict:

    print_header(
        f"PAGE {page_number} - STEP 4 - DUAL TTS"
    )

    if not text.strip():

        raise RuntimeError(
            "TTS input text is empty."
        )

    female_voice = os.getenv(
        "GEMINI_TTS_FEMALE_VOICE",
        "Leda",
    )

    male_voice = os.getenv(
        "GEMINI_TTS_MALE_VOICE",
        "Orus",
    )

    tts_dir = (
        output_dir
        / "tts"
    )

    # ========================================================
    # FEMALE
    # ========================================================

    print_header(
        f"PAGE {page_number} - "
        f"FEMALE - {female_voice}"
    )

    female_dir = (
        tts_dir
        / "female"
    )

    female_pipeline = (
        TTSPipeline(
            output_dir=female_dir,
            max_chars=700,
            voice=female_voice,
            narrator_type="female",
        )
    )

    female_result = (
        female_pipeline.run(
            text=text,
            input_name=(
                f"{input_name}_female"
            ),
        )
    )

    if not female_result.final_audio_path:

        raise RuntimeError(
            "Female TTS failed."
        )

    female_audio = Path(
        female_result.final_audio_path
    )

    ensure_exists(
        female_audio,
        "Female audio",
    )

    # ========================================================
    # MALE
    # ========================================================

    print_header(
        f"PAGE {page_number} - "
        f"MALE - {male_voice}"
    )

    male_dir = (
        tts_dir
        / "male"
    )

    male_pipeline = (
        TTSPipeline(
            output_dir=male_dir,
            max_chars=700,
            voice=male_voice,
            narrator_type="male",
        )
    )

    male_result = (
        male_pipeline.run(
            text=text,
            input_name=(
                f"{input_name}_male"
            ),
        )
    )

    if not male_result.final_audio_path:

        raise RuntimeError(
            "Male TTS failed."
        )

    male_audio = Path(
        male_result.final_audio_path
    )

    ensure_exists(
        male_audio,
        "Male audio",
    )

    tts_report = {
        "female": {
            "voice":
                female_voice,

            "model":
                female_result.model,

            "audio":
                str(
                    female_audio
                ),

            "duration_seconds":
                female_result
                .total_duration_seconds,
        },

        "male": {
            "voice":
                male_voice,

            "model":
                male_result.model,

            "audio":
                str(
                    male_audio
                ),

            "duration_seconds":
                male_result
                .total_duration_seconds,
        },
    }

    report_path = (
        tts_dir
        / "dual_tts_report.json"
    )

    save_json(
        report_path,
        tts_report,
    )

    print()
    print("=" * 80)
    print("DUAL TTS COMPLETED")
    print("=" * 80)

    print(
        f"Female : "
        f"{female_voice}"
    )

    print(
        f"Audio  : "
        f"{female_audio}"
    )

    print()

    print(
        f"Male   : "
        f"{male_voice}"
    )

    print(
        f"Audio  : "
        f"{male_audio}"
    )

    return {
        "female_audio":
            female_audio,

        "female_voice":
            female_voice,

        "female_duration":
            female_result
            .total_duration_seconds,

        "male_audio":
            male_audio,

        "male_voice":
            male_voice,

        "male_duration":
            male_result
            .total_duration_seconds,

        "report_path":
            report_path,
    }


# ============================================================
# PROCESS ONE PAGE
# ============================================================


def process_page(
    input_file: Path,
    book_output_dir: Path,
    page_number: int,
    dpi: int,
    enable_final_context_review: bool = False,
) -> dict:
    """
    Complete one-page pipeline.

    كل صفحة معزولة تمامًا في:

        page_001/
        page_002/
        ...
    """

    page_dir = (
        book_output_dir
        / f"page_{page_number:03d}"
    )

    page_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    page_status = {
        "page":
            page_number,

        "status":
            "PROCESSING",

        "stages": {
            "original_page":
                False,

            "ocr":
                False,

            "final_review":
                False,

            "diacritization":
                False,

            "female_tts":
                False,

            "male_tts":
                False,
        },
    }

    status_path = (
        page_dir
        / "page_status.json"
    )

    save_json(
        status_path,
        page_status,
    )

    # ========================================================
    # ORIGINAL PAGE
    # ========================================================

    (
        page_image,
        original_page_path,
    ) = render_and_save_original_page(
        input_file=input_file,
        page_number=page_number,
        dpi=dpi,
        page_dir=page_dir,
    )

    page_status[
        "stages"
    ][
        "original_page"
    ] = True

    save_json(
        status_path,
        page_status,
    )

    # ========================================================
    # OCR
    # ========================================================

    ocr_result = (
        run_ocr_pipeline(
            input_file=input_file,
            output_dir=page_dir,
            page_number=page_number,
            dpi=dpi,
        )
    )

    corrected_text = read_text_file(
        ocr_result[
            "corrected_path"
        ]
    )

    if not corrected_text:

        raise RuntimeError(
            f"Page {page_number}: "
            "OCR corrected text is empty."
        )

    page_status[
        "stages"
    ][
        "ocr"
    ] = True

    save_json(
        status_path,
        page_status,
    )

    # ========================================================
    # FINAL REVIEW
    # ========================================================

    if enable_final_context_review:

        final_review = (
            run_final_context_review(
                corrected_text=corrected_text,
                output_dir=page_dir,
                page_number=page_number,
            )
        )

    else:
        # --------------------------------------------------------
        # SAFE DEFAULT
        # --------------------------------------------------------
        # The initial OCR pipeline already performs:
        # context detection + crop verification + independent visual gate.
        # A second text-only model must not silently rewrite letters.
        # Therefore final text-only review is OFF by default.
        # This both prevents regressions (e.g. names) and saves API.
        final_review_dir = (
            page_dir
            / "final_review"
        )
        final_review_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        before_path = (
            final_review_dir
            / "before_final_review.txt"
        )
        final_path = (
            final_review_dir
            / "final_corrected_text.txt"
        )

        before_path.write_text(
            corrected_text,
            encoding="utf-8",
        )
        final_path.write_text(
            corrected_text,
            encoding="utf-8",
        )

        passthrough_report = {
            "changed": False,
            "attempts": 0,
            "changed_words": 0,
            "changed_ratio": 0.0,
            "review_required": False,
            "rejection_reason": None,
            "mode": "VISUAL_VERIFIED_OCR_PASSTHROUGH",
        }

        final_report_path = (
            final_review_dir
            / "report.json"
        )
        save_json(
            final_report_path,
            passthrough_report,
        )

        final_review = {
            "path": final_path,
            "text": corrected_text,
            "changed": False,
            "changed_words": 0,
            "attempts": 0,
            "review_required": False,
            "rejection_reason": None,
            "changed_ratio": 0.0,
            "report_path": final_report_path,
        }

        print(
            "[FINAL REVIEW] SKIPPED BY SAFE DEFAULT "
            "- using visually verified OCR text"
        )

    final_corrected_text = (
        final_review[
            "text"
        ].strip()
    )

    if not final_corrected_text:

        raise RuntimeError(
            "Final corrected text "
            "is empty."
        )

    page_status[
        "stages"
    ][
        "final_review"
    ] = True

    save_json(
        status_path,
        page_status,
    )

    # ========================================================
    # FINAL REVIEW QUALITY GATE
    # ========================================================
    #
    # Important cost rule:
    # If the text-only final reviewer says the page still needs
    # visual review, DO NOT spend money on diacritization/TTS yet.
    #
    # The page keeps the verified OCR text and stops safely here.
    # ========================================================

    if final_review.get(
        "review_required",
        False,
    ):
        reason = (
            final_review.get(
                "rejection_reason"
            )
            or "Final review requires visual verification."
        )

        page_status[
            "status"
        ] = "REVIEW_REQUIRED"

        page_status[
            "review_reason"
        ] = reason

        save_json(
            status_path,
            page_status,
        )

        review_gate_path = (
            page_dir
            / "FINAL_REVIEW_REQUIRED.txt"
        )

        review_gate_path.write_text(
            (
                "FINAL REVIEW REQUIRES VISUAL VERIFICATION\n\n"
                + str(reason)
                + "\n\n"
                + "Diacritization and TTS were intentionally skipped "
                + "to avoid spending API on unverified text.\n"
            ),
            encoding="utf-8",
        )

        print()
        print(
            "[QUALITY GATE] REVIEW_REQUIRED"
        )

        print(
            "[QUALITY GATE] "
            "Skipping diacritization and TTS "
            "until the text is visually verified."
        )

        return {
            "page":
                page_number,

            "status":
                "REVIEW_REQUIRED",

            "files": {
                "original_page":
                    str(
                        original_page_path
                    ),

                "original_ocr":
                    str(
                        ocr_result[
                            "original_ocr_path"
                        ]
                    )
                    if ocr_result[
                        "original_ocr_path"
                    ]
                    else None,

                "corrected_text":
                    str(
                        ocr_result[
                            "corrected_path"
                        ]
                    ),

                "final_corrected_text":
                    str(
                        final_review[
                            "path"
                        ]
                    ),

                "final_diacritized_text":
                    None,

                "diacritics_review":
                    None,

                "female_audio":
                    None,

                "male_audio":
                    None,
            },

            "ocr":
                ocr_result.get(
                    "stats",
                    {},
                )
                or {},

            "final_review": {
                "changed":
                    final_review[
                        "changed"
                    ],

                "changed_words":
                    final_review[
                        "changed_words"
                    ],

                "changed_ratio":
                    final_review.get(
                        "changed_ratio",
                        0.0,
                    ),

                "attempts":
                    final_review[
                        "attempts"
                    ],

                "review_required":
                    True,

                "rejection_reason":
                    reason,
            },

            "diacritization": {
                "total_words": 0,
                "review_words": 0,
            },

            "tts": {
                "female": {
                    "voice":
                        os.getenv(
                            "GEMINI_TTS_FEMALE_VOICE",
                            "Leda",
                        ),
                    "duration_seconds": 0.0,
                },
                "male": {
                    "voice":
                        os.getenv(
                            "GEMINI_TTS_MALE_VOICE",
                            "Orus",
                        ),
                    "duration_seconds": 0.0,
                },
            },

            "metrics": {
                "correction_rate_percent": 0.0,
            },
        }

    # ========================================================
    # DIACRITIZATION
    # ========================================================

    diacritics = (
        run_diacritization(
            corrected_text=(
                final_corrected_text
            ),
            page_image=page_image,
            output_dir=page_dir,
            page_number=page_number,
        )
    )

    final_diacritized_text = (
        diacritics[
            "text"
        ].strip()
    )

    page_status[
        "stages"
    ][
        "diacritization"
    ] = True

    save_json(
        status_path,
        page_status,
    )

    # ========================================================
    # DIACRITICS QUALITY GATE BEFORE PAID TTS
    # ========================================================

    diacritics_total = int(
        diacritics.get(
            "total_words",
            0,
        )
        or 0
    )
    diacritics_review_count = int(
        diacritics.get(
            "review_words",
            0,
        )
        or 0
    )
    diacritics_review_rate = (
        diacritics_review_count
        / diacritics_total
    ) if diacritics_total else 0.0

    # Extremely uncertain shaping should not be converted to paid audio.
    # 50% is intentionally conservative so normal REVIEW rates still pass.
    if diacritics_review_rate >= 0.50:
        reason = (
            "Diacritics review rate too high for automatic TTS: "
            f"{diacritics_review_count}/{diacritics_total} "
            f"({diacritics_review_rate:.1%})"
        )

        page_status["status"] = (
            "DIACRITICS_REVIEW_REQUIRED"
        )
        page_status["review_reason"] = reason
        save_json(
            status_path,
            page_status,
        )

        print(
            "[DIACRITICS QUALITY GATE] "
            + reason
        )
        print(
            "[DIACRITICS QUALITY GATE] "
            "Skipping Leda/Orus to avoid paying for unverified text."
        )

        return {
            "page": page_number,
            "status": "DIACRITICS_REVIEW_REQUIRED",
            "files": {
                "original_page": str(original_page_path),
                "original_ocr": (
                    str(ocr_result["original_ocr_path"])
                    if ocr_result["original_ocr_path"]
                    else None
                ),
                "corrected_text": str(ocr_result["corrected_path"]),
                "final_corrected_text": str(final_review["path"]),
                "final_diacritized_text": str(diacritics["path"]),
                "diacritics_review": str(diacritics["review_path"]),
                "female_audio": None,
                "male_audio": None,
            },
            "ocr": ocr_result.get("stats", {}) or {},
            "final_review": {
                "changed": final_review["changed"],
                "changed_words": final_review["changed_words"],
                "attempts": final_review["attempts"],
            },
            "diacritization": {
                "total_words": diacritics_total,
                "review_words": diacritics_review_count,
            },
            "tts": {
                "female": {
                    "voice": os.getenv("GEMINI_TTS_FEMALE_VOICE", "Leda"),
                    "duration_seconds": 0.0,
                },
                "male": {
                    "voice": os.getenv("GEMINI_TTS_MALE_VOICE", "Orus"),
                    "duration_seconds": 0.0,
                },
            },
            "metrics": {
                "correction_rate_percent": 0.0,
            },
        }

    # ========================================================
    # TTS
    # ========================================================

    input_name = (
        f"{input_file.stem}"
        f"_page_{page_number:03d}"
    )

    tts = run_dual_tts(
        text=(
            final_diacritized_text
        ),
        output_dir=page_dir,
        input_name=input_name,
        page_number=page_number,
    )

    page_status[
        "stages"
    ][
        "female_tts"
    ] = True

    page_status[
        "stages"
    ][
        "male_tts"
    ] = True

    page_status[
        "status"
    ] = "SUCCESS"

    # ========================================================
    # PAGE REPORT
    # ========================================================

    initial_stats = (
        ocr_result.get(
            "stats",
            {},
        )
        or {}
    )

    total_words = int(
        initial_stats.get(
            "total_words",
            0,
        )
        or 0
    )

    suspicious_words = int(
        initial_stats.get(
            "suspicious_words",
            0,
        )
        or 0
    )

    accepted = int(
        initial_stats.get(
            "accepted",
            0,
        )
        or 0
    )

    rejected = int(
        initial_stats.get(
            "rejected",
            0,
        )
        or 0
    )

    manual_review = int(
        initial_stats.get(
            "manual_review",
            0,
        )
        or 0
    )

    final_changed_words = int(
        final_review[
            "changed_words"
        ]
    )

    # Metric، مش Ground Truth accuracy.
    correction_rate = (
        (
            accepted
            + final_changed_words
        )
        / total_words
        * 100
    ) if total_words else 0.0

    page_report = {
        "page":
            page_number,

        "status":
            "SUCCESS",

        "files": {
            "original_page":
                str(
                    original_page_path
                ),

            "original_ocr":
                str(
                    ocr_result[
                        "original_ocr_path"
                    ]
                )
                if ocr_result[
                    "original_ocr_path"
                ]
                else None,

            "corrected_text":
                str(
                    ocr_result[
                        "corrected_path"
                    ]
                ),

            "final_corrected_text":
                str(
                    final_review[
                        "path"
                    ]
                ),

            "final_diacritized_text":
                str(
                    diacritics[
                        "path"
                    ]
                ),

            "diacritics_review":
                str(
                    diacritics[
                        "review_path"
                    ]
                ),

            "female_audio":
                str(
                    tts[
                        "female_audio"
                    ]
                ),

            "male_audio":
                str(
                    tts[
                        "male_audio"
                    ]
                ),
        },

        "ocr": {
            "total_words":
                total_words,

            "suspicious_words":
                suspicious_words,

            "accepted":
                accepted,

            "rejected":
                rejected,

            "manual_review":
                manual_review,
        },

        "final_review": {
            "changed":
                final_review[
                    "changed"
                ],

            "changed_words":
                final_changed_words,

            "attempts":
                final_review[
                    "attempts"
                ],
        },

        "diacritization": {
            "total_words":
                diacritics[
                    "total_words"
                ],

            "review_words":
                diacritics[
                    "review_words"
                ],
        },

        "tts": {
            "female": {
                "voice":
                    tts[
                        "female_voice"
                    ],

                "duration_seconds":
                    tts[
                        "female_duration"
                    ],
            },

            "male": {
                "voice":
                    tts[
                        "male_voice"
                    ],

                "duration_seconds":
                    tts[
                        "male_duration"
                    ],
            },
        },

        "metrics": {
            "correction_rate_percent":
                round(
                    correction_rate,
                    2,
                )
        },
    }

    page_report_path = (
        page_dir
        / "page_report.json"
    )

    save_json(
        page_report_path,
        page_report,
    )

    page_status[
        "report"
    ] = str(
        page_report_path
    )

    save_json(
        status_path,
        page_status,
    )

    return page_report


# ============================================================
# FAILED PAGE REPORT
# ============================================================


def make_failed_page_report(
    page_number: int,
    page_dir: Path,
    exc: Exception,
) -> dict:

    error_text = str(
        exc
    )

    trace_text = (
        traceback.format_exc()
    )

    error_path = (
        page_dir
        / "error.txt"
    )

    error_path.write_text(
        (
            error_text
            + "\n\n"
            + trace_text
        ),
        encoding="utf-8",
    )

    status = {
        "page":
            page_number,

        "status":
            "FAILED",

        "error":
            error_text,

        "error_file":
            str(
                error_path
            ),
    }

    save_json(
        page_dir
        / "page_status.json",
        status,
    )

    return status


# ============================================================
# BOOK REPORT
# ============================================================


def build_book_report(
    input_file: Path,
    pages_requested: list[int],
    page_results: list[dict],
    output_dir: Path,
) -> dict:

    successful = [
        item
        for item in page_results
        if item.get(
            "status"
        ) == "SUCCESS"
    ]

    review_required = [
        item
        for item in page_results
        if item.get(
            "status"
        ) in {
            "REVIEW_REQUIRED",
            "DIACRITICS_REVIEW_REQUIRED",
        }
    ]

    failed = [
        item
        for item in page_results
        if item.get(
            "status"
        ) == "FAILED"
    ]

    total_words = sum(
        item.get(
            "ocr",
            {},
        ).get(
            "total_words",
            0,
        )
        for item in successful
    )

    suspicious_words = sum(
        item.get(
            "ocr",
            {},
        ).get(
            "suspicious_words",
            0,
        )
        for item in successful
    )

    accepted = sum(
        item.get(
            "ocr",
            {},
        ).get(
            "accepted",
            0,
        )
        for item in successful
    )

    rejected = sum(
        item.get(
            "ocr",
            {},
        ).get(
            "rejected",
            0,
        )
        for item in successful
    )

    manual_review = sum(
        item.get(
            "ocr",
            {},
        ).get(
            "manual_review",
            0,
        )
        for item in successful
    )

    final_review_corrections = sum(
        item.get(
            "final_review",
            {},
        ).get(
            "changed_words",
            0,
        )
        for item in successful
    )

    diacritics_total_words = sum(
        item.get(
            "diacritization",
            {},
        ).get(
            "total_words",
            0,
        )
        for item in successful
    )

    diacritics_review_words = sum(
        item.get(
            "diacritization",
            {},
        ).get(
            "review_words",
            0,
        )
        for item in successful
    )

    female_audio_ready = sum(
        1
        for item in successful
        if item.get(
            "files",
            {},
        ).get(
            "female_audio"
        )
    )

    male_audio_ready = sum(
        1
        for item in successful
        if item.get(
            "files",
            {},
        ).get(
            "male_audio"
        )
    )

    female_total_duration = sum(
        item.get(
            "tts",
            {},
        ).get(
            "female",
            {},
        ).get(
            "duration_seconds",
            0,
        )
        or 0
        for item in successful
    )

    male_total_duration = sum(
        item.get(
            "tts",
            {},
        ).get(
            "male",
            {},
        ).get(
            "duration_seconds",
            0,
        )
        or 0
        for item in successful
    )

    final_total_corrections = (
        accepted
        + final_review_corrections
    )

    correction_rate = (
        final_total_corrections
        / total_words
        * 100
    ) if total_words else 0.0

    suspicious_rate = (
        suspicious_words
        / total_words
        * 100
    ) if total_words else 0.0

    diacritics_review_rate = (
        diacritics_review_words
        / diacritics_total_words
        * 100
    ) if diacritics_total_words else 0.0

    report = {
        "book": {
            "name":
                input_file.stem,

            "input_pdf":
                str(
                    input_file
                ),

            "pages_requested":
                pages_requested,

            "pages_requested_count":
                len(
                    pages_requested
                ),

            "pages_successful":
                len(
                    successful
                ),

            "pages_failed":
                len(
                    failed
                ),

            "pages_review_required":
                len(
                    review_required
                ),
        },

        "ocr": {
            "total_words":
                total_words,

            "suspicious_words":
                suspicious_words,

            "accepted":
                accepted,

            "rejected":
                rejected,

            "manual_review":
                manual_review,

            "suspicious_rate_percent":
                round(
                    suspicious_rate,
                    2,
                ),
        },

        "final_review": {
            "changed_words":
                final_review_corrections,

            "initial_accepted_plus_final_changes":
                final_total_corrections,

            "correction_rate_percent":
                round(
                    correction_rate,
                    2,
                ),
        },

        "diacritization": {
            "total_words":
                diacritics_total_words,

            "review_words":
                diacritics_review_words,

            "review_rate_percent":
                round(
                    diacritics_review_rate,
                    2,
                ),
        },

        "tts": {
            "female": {
                "voice":
                    os.getenv(
                        "GEMINI_TTS_FEMALE_VOICE",
                        "Leda",
                    ),

                "pages_ready":
                    female_audio_ready,

                "total_duration_seconds":
                    round(
                        female_total_duration,
                        2,
                    ),
            },

            "male": {
                "voice":
                    os.getenv(
                        "GEMINI_TTS_MALE_VOICE",
                        "Orus",
                    ),

                "pages_ready":
                    male_audio_ready,

                "total_duration_seconds":
                    round(
                        male_total_duration,
                        2,
                    ),
            },
        },

        "pages":
            page_results,

        "review_required_pages": [
            item.get(
                "page"
            )
            for item in review_required
        ],

        "failed_pages": [
            item.get(
                "page"
            )
            for item in failed
        ],
    }

    report_path = (
        output_dir
        / "book_report.json"
    )

    save_json(
        report_path,
        report,
    )

    return report


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Arabic Book Full Pipeline\n"
            "OCR -> Initial Review -> "
            "Gemini Pro Final Review -> "
            "Visual Diacritics -> "
            "Leda + Orus TTS"
        )
    )

    parser.add_argument(
        "input_file",
        type=Path,
        help="Input PDF",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(
            "full_pipeline_output"
        ),
        help="Book output directory",
    )

    parser.add_argument(
        "--pages",
        type=str,
        required=True,
        help=(
            "Examples: "
            "3 | 1-16 | 1,3,5 | 1-4,8-12"
        ),
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=220,
        help="PDF rendering DPI",
    )

    parser.add_argument(
        "--enable-final-context-review",
        action="store_true",
        help=(
            "Optional text-only final audit. "
            "Disabled by default because automatic letter changes "
            "without image evidence are unsafe and cost extra API."
        ),
    )

    args = parser.parse_args()

    # ========================================================
    # ABSOLUTE PATHS
    # ========================================================

    input_file = (
        args.input_file
        .expanduser()
        .resolve()
    )

    output_dir = (
        args.output
        .expanduser()
        .resolve()
    )

    ensure_exists(
        input_file,
        "Input PDF",
    )

    if (
        input_file
        .suffix
        .lower()
        != ".pdf"
    ):

        raise RuntimeError(
            "Input must be a PDF."
        )

    pages = parse_pages(
        args.pages
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # HEADER
    # ========================================================

    print_header(
        "ARABIC BOOK FULL PIPELINE"
    )

    print(
        f"Book   : "
        f"{input_file.name}"
    )

    print(
        f"Pages  : "
        f"{pages}"
    )

    print(
        f"Count  : "
        f"{len(pages)}"
    )

    print(
        f"DPI    : "
        f"{args.dpi}"
    )

    print(
        f"Output : "
        f"{output_dir}"
    )

    print()

    print(
        "Female : "
        + os.getenv(
            "GEMINI_TTS_FEMALE_VOICE",
            "Leda",
        )
    )

    print(
        "Male   : "
        + os.getenv(
            "GEMINI_TTS_MALE_VOICE",
            "Orus",
        )
    )

    # ========================================================
    # PROCESS BOOK
    # ========================================================

    page_results: list[dict] = []

    for index, page_number in enumerate(
        pages,
        start=1,
    ):

        print()
        print()
        print("#" * 80)

        print(
            f"BOOK PROGRESS "
            f"{index}/{len(pages)} "
            f"- PAGE {page_number}"
        )

        print("#" * 80)

        page_dir = (
            output_dir
            / f"page_{page_number:03d}"
        )

        page_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:

            result = process_page(
                input_file=input_file,
                book_output_dir=output_dir,
                page_number=page_number,
                dpi=args.dpi,
                enable_final_context_review=(
                    args.enable_final_context_review
                ),
            )

            page_results.append(
                result
            )

            print()

            if (
                result.get(
                    "status"
                )
                == "REVIEW_REQUIRED"
            ):
                print(
                    f"[PAGE {page_number}] "
                    "REVIEW_REQUIRED "
                    "- downstream API stages skipped"
                )
            else:
                print(
                    f"[PAGE {page_number}] "
                    "SUCCESS"
                )

        except Exception as exc:

            print()
            print(
                f"[PAGE {page_number}] "
                f"FAILED: {exc}"
            )

            failed_result = (
                make_failed_page_report(
                    page_number=page_number,
                    page_dir=page_dir,
                    exc=exc,
                )
            )

            page_results.append(
                failed_result
            )

            print(
                "[CONTINUE] "
                "Moving to next page..."
            )

        # ====================================================
        # SAVE PROGRESS AFTER EVERY PAGE
        # ====================================================

        build_book_report(
            input_file=input_file,
            pages_requested=pages,
            page_results=page_results,
            output_dir=output_dir,
        )

    # ========================================================
    # FINAL BOOK REPORT
    # ========================================================

    report = build_book_report(
        input_file=input_file,
        pages_requested=pages,
        page_results=page_results,
        output_dir=output_dir,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print_header(
        "BOOK PIPELINE COMPLETED"
    )

    print(
        f"Book:"
        f" {report['book']['name']}"
    )

    print(
        f"Pages requested:"
        f" {report['book']['pages_requested_count']}"
    )

    print(
        f"Pages successful:"
        f" {report['book']['pages_successful']}"
    )

    print(
        f"Pages failed:"
        f" {report['book']['pages_failed']}"
    )

    print(
        f"Pages review required:"
        f" {report['book'].get('pages_review_required', 0)}"
    )

    print()
    print(
        "OCR"
    )

    print(
        f"Total Words       : "
        f"{report['ocr']['total_words']}"
    )

    print(
        f"Suspicious Words  : "
        f"{report['ocr']['suspicious_words']}"
    )

    print(
        f"Accepted          : "
        f"{report['ocr']['accepted']}"
    )

    print(
        f"Rejected          : "
        f"{report['ocr']['rejected']}"
    )

    print(
        f"Manual Review     : "
        f"{report['ocr']['manual_review']}"
    )

    print()
    print(
        "FINAL AI REVIEW"
    )

    print(
        f"Changed Words     : "
        f"{report['final_review']['changed_words']}"
    )

    print(
        f"Correction Rate   : "
        f"{report['final_review']['correction_rate_percent']:.2f}%"
    )

    print()
    print(
        "DIACRITIZATION"
    )

    print(
        f"Total Words       : "
        f"{report['diacritization']['total_words']}"
    )

    print(
        f"Review Words      : "
        f"{report['diacritization']['review_words']}"
    )

    print(
        f"Review Rate       : "
        f"{report['diacritization']['review_rate_percent']:.2f}%"
    )

    print()
    print(
        "AUDIO"
    )

    print(
        f"Female "
        f"({report['tts']['female']['voice']}) "
        f": "
        f"{report['tts']['female']['pages_ready']}/"
        f"{report['book']['pages_requested_count']}"
    )

    print(
        f"Male "
        f"({report['tts']['male']['voice']}) "
        f": "
        f"{report['tts']['male']['pages_ready']}/"
        f"{report['book']['pages_requested_count']}"
    )

    print()

    print(
        f"Book report:"
    )

    print(
        output_dir
        / "book_report.json"
    )

    if report.get(
        "review_required_pages"
    ):

        print()
        print(
            "Pages requiring visual review:"
        )

        print(
            report[
                "review_required_pages"
            ]
        )

        print()
        print(
            "Diacritization/TTS were skipped "
            "for these pages to save API cost."
        )

    if report[
        "failed_pages"
    ]:

        print()
        print(
            "Failed pages:"
        )

        print(
            report[
                "failed_pages"
            ]
        )

        print()
        print(
            "You can rerun only "
            "the failed pages later."
        )

    print()
    print(
        "DONE"
    )


if __name__ == "__main__":

    main()