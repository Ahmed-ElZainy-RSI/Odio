"""Layers 01-04 for a scanned book via LightOn. Resumable per page.

No bounding boxes from this engine (box_level 'none') -- build_reader.py
drops the page-image view entirely for books extracted this way.

"Already done" means status 'extracted' *or* anything later ('diacritised',
'spoken') -- checking only the literal string 'extracted' would re-OCR (and
silently reset to 'extracted') any page a later stage already finished.
That matters especially here: tools/apply_corrections.py and
tools/replace_page.py deliberately reset a corrected page's status to
'extracted' so run_tashkel.py/run_speak_gemini.py resume it -- re-running
this against that same book would instead wipe every applied correction
and re-OCR from scratch, undoing real (paid) vlm_arbiter.py work. See
run_extract.py's docstring for this exact class of bug happening for real
on the text-layer path, and run_ocr_gemini.py for the OCR-path version.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

from pypdf import PdfReader
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import failures
from pipeline.records import write_record
from pipeline.extract import clean_word, diacritic_ratio
from pipeline.ocr_lighton import (slice_page, parse_page, clean_markdown,
                                   text_to_tokens, ENGINE)
from pipeline.pages import parse_pages

DIAC_THRESHOLD = 0.15


def run(pdf_path: Path, out_dir: Path, pages="0"):
    """OCR pdf_path into out_dir. pages: "0" = all, "5" = pages 1-5, "13:20" = pages 13-20."""
    book = pdf_path.stem
    key = os.environ["LIGHTON_API_KEY"]
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    start, end = parse_pages(pages)
    print(f"{book} — {total} pages via {ENGINE}, doing {start}-{end or total}")
    print("box_level=none: no geometry from this engine, so no page-image view\n")
    print(f"{'pg':>3} {'tokens':>7} {'chars':>6} {'diac':>6} {'fixed':>6}  path")
    print("-" * 52)

    summary = {"done": 0, "failed": 0, "stopped_on_quota": False}
    for i in range(start, (end or total) + 1):
        rec_path = out_dir / f"p{i:03d}.json"
        if rec_path.exists() and json.loads(rec_path.read_text(encoding="utf-8")).get("status") in (
                "extracted", "diacritised", "spoken"):
            print(f"{i:>3}  already done, skipping")
            continue
        try:
            md = parse_page(slice_page(reader, i - 1), key, f"p{i:03d}.pdf")
        except Exception as e:
            # a page-level failure is recorded and skipped; only quota stops the run (spec 008)
            if failures.handle(out_dir, i, "ocr", e, f"{i:>3}") == "quota":
                summary["stopped_on_quota"] = True
                break
            summary["failed"] += 1
            continue

        text = clean_markdown(md)
        tokens = text_to_tokens(text, i, clean_word)
        joined = " ".join(t["txt"] for t in tokens)
        ratio = diacritic_ratio(joined)
        fixed = sum(1 for t in tokens if t["fixes"])
        rec = {
            "book": book, "source_pdf": pdf_path.name, "pdf_sha256_16": pdf_hash,
            "page": i, "source": "ocr", "ocr_engine": ENGINE,
            "box_level": "none",
            "diac_ratio": round(ratio, 3),
            "path": "keep" if ratio >= DIAC_THRESHOLD else "diacritise",
            "status": "extracted", "tokens": tokens,
        }
        write_record(rec_path, rec)
        failures.clear(out_dir, i, "ocr")
        summary["done"] += 1
        print(f"{i:>3} {len(tokens):>7} {len(joined):>6} {ratio:>6.2f} {fixed:>6}  {rec['path']}")
    print(f"\n{summary['done']} extracted, {summary['failed']} failed"
          + (" -- stopped on quota" if summary["stopped_on_quota"] else ""))
    return summary


def main():
    book = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
    # standalone CLI keeps the -lighton suffix so the Gemini and LightOn
    # extractions of the same book can be compared side by side
    run(Path(f"assets/PDF Samples/{book}.pdf"), Path(f"work/{book.strip()}-lighton"),
        pages=os.environ.get("PAGES", "0"))


if __name__ == "__main__":
    main()
