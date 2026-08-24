from __future__ import annotations

import argparse
import re
from pathlib import Path

import pymupdf


ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
VISIBLE_TEXT_RE = re.compile(r"[A-Za-z0-9\u0600-\u06FF]")


def analyze_page(page) -> dict:
    """
    يفحص صفحة واحدة ويقرر:
    - TEXT_LAYER
    - OCR_REQUIRED
    """

    text = page.get_text("text").strip()

    total_chars = len(text)
    visible_chars = len(VISIBLE_TEXT_RE.findall(text))
    arabic_chars = len(ARABIC_RE.findall(text))
    words = text.split()

    # هل الصفحة تحتوي صور؟
    images = page.get_images(full=True)
    image_count = len(images)

    # ---------------------------------------------------------
    # قواعد القرار
    # ---------------------------------------------------------

    if total_chars == 0:
        decision = "OCR_REQUIRED"
        reason = "No text layer found"

    elif visible_chars < 15:
        decision = "OCR_REQUIRED"
        reason = "Text layer exists but contains too little usable text"

    elif len(words) < 3:
        decision = "OCR_REQUIRED"
        reason = "Very few words detected"

    else:
        decision = "TEXT_LAYER"
        reason = "Usable embedded text found"

    return {
        "text_chars": total_chars,
        "visible_chars": visible_chars,
        "arabic_chars": arabic_chars,
        "words": len(words),
        "images": image_count,
        "decision": decision,
        "reason": reason,
    }


def inspect_pdf(pdf_path: str):
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = pymupdf.open(pdf_path)

    print("=" * 70)
    print(f"PDF: {pdf_path.name}")
    print(f"Pages: {len(doc)}")
    print("=" * 70)

    text_layer_pages = 0
    ocr_pages = 0

    results = []

    for page_number, page in enumerate(doc, start=1):

        info = analyze_page(page)

        results.append(
            {
                "page": page_number,
                **info,
            }
        )

        if info["decision"] == "TEXT_LAYER":
            text_layer_pages += 1
            icon = "OK"
        else:
            ocr_pages += 1
            icon = "OCR"

        print(
            f"Page {page_number:03d} | "
            f"{icon:3s} | "
            f"{info['decision']:12s} | "
            f"Words: {info['words']:4d} | "
            f"Chars: {info['text_chars']:5d} | "
            f"Images: {info['images']:2d}"
        )

        print(f"          Reason: {info['reason']}")

    doc.close()

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"Text Layer pages : {text_layer_pages}")
    print(f"OCR pages        : {ocr_pages}")

    if ocr_pages == 0:
        pdf_decision = "TEXT_LAYER"

    elif text_layer_pages == 0:
        pdf_decision = "OCR_REQUIRED"

    else:
        pdf_decision = "MIXED_PDF"

    print()
    print(f"PDF TYPE: {pdf_decision}")

    return {
        "pdf": str(pdf_path),
        "pages": len(results),
        "text_layer_pages": text_layer_pages,
        "ocr_pages": ocr_pages,
        "pdf_type": pdf_decision,
        "page_results": results,
    }


def main():

    parser = argparse.ArgumentParser(
        description="Detect whether a PDF contains usable text layer or requires OCR."
    )

    parser.add_argument(
        "pdf",
        help="Path to PDF file",
    )

    args = parser.parse_args()

    inspect_pdf(args.pdf)


if __name__ == "__main__":
    main()