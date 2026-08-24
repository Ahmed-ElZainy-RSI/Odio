from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image

from ocr_verifier.lighton_ocr import LightOnOCR
from ocr_verifier.diacritization.word_diacritics import (
    WordDiacriticsExtractor,
)


def clean_ocr_output(text: str) -> str:

    if not text:
        return ""

    # Remove LightOn notes
    text = re.sub(
        r"\n*\s*Note:.*$",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove HTML
    text = re.sub(
        r"<[^>]+>",
        "",
        text,
    )

    # Remove standalone page number
    text = re.sub(
        r"^\s*[0-9٠-٩]+\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    return text.strip()


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "image",
        type=Path,
    )

    args = parser.parse_args()

    if not args.image.exists():
        raise SystemExit(
            f"Image not found: {args.image}"
        )

    # ---------------------------------------------------------
    # LOAD IMAGE
    # ---------------------------------------------------------

    print("=" * 70)
    print("WORD-BY-WORD VISUAL DIACRITICS TEST")
    print("=" * 70)

    with Image.open(args.image) as source:

        image = (
            source
            .convert("RGB")
            .copy()
        )

    print(
        f"[IMAGE] {image.width}x{image.height}"
    )

    # ---------------------------------------------------------
    # LIGHTON
    # ---------------------------------------------------------

    print()
    print("[1/2] LIGHTON OCR")

    lighton = LightOnOCR()

    raw_text = lighton.ocr_image(
        image
    )

    clean_text = clean_ocr_output(
        raw_text
    )

    print()
    print("REFERENCE TEXT")
    print("-" * 70)
    print(clean_text)
    print("-" * 70)

    # ---------------------------------------------------------
    # WORD DIACRITICS
    # ---------------------------------------------------------

    print()
    print("[2/2] WORD DIACRITICS")

    extractor = WordDiacriticsExtractor()

    output_dir = Path(
        "word_diacritics_test"
    )

    debug_dir = (
        output_dir / "words"
    )

    results = extractor.extract_page(
        page_image=image,
        corrected_text=clean_text,
        debug_dir=debug_dir,
    )

    # ---------------------------------------------------------
    # RESULT
    # ---------------------------------------------------------

    final_words = (
        extractor.build_word_sequence(
            results
        )
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = (
        output_dir
        / "result.txt"
    )

    output_file.write_text(
        final_words,
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)

    print(final_words)

    print()
    print(
        f"Processed words: {len(results)}"
    )

    review_count = sum(
        1
        for result in results
        if result.status == "REVIEW"
    )

    print(
        f"Review words: {review_count}"
    )

    print(
        f"Output: {output_file}"
    )


if __name__ == "__main__":
    main()