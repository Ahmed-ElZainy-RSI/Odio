"""Layers 01-04 for a scanned book: Gemini OCR -> tokenise -> clean -> route.

Resumable: a page whose record already exists past this stage is skipped,
so a quota stall costs one page on retry rather than the whole book. "Past
this stage" means status 'extracted' *or* anything later in the pipeline
('diacritised', 'spoken') -- checking only the literal string 'extracted'
would re-OCR (and silently reset to 'extracted', discarding diacritization
and audio) any page a later stage already finished, exactly the class of
bug run_tashkel.py's own docstring describes and was fixed for there but
not here until this comment.

A page that fails (unrenderable, malformed model response) is recorded in
failures.json and the run moves on; only a quota error stops it (spec 008,
see failures.py).
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import fitz
from dotenv import load_dotenv
load_dotenv()
from google import genai

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import failures
from pipeline.extract import clean_word, diacritic_ratio
from pipeline.ocr_gemini import render_page, ocr_page, lines_to_tokens, MODEL
from pipeline.pages import parse_pages

DIAC_THRESHOLD = 0.15


def run(pdf_path: Path, out_dir: Path, pages="0"):
    """OCR pdf_path into out_dir. pages: "0" = all, "5" = pages 1-5, "13:20" = pages 13-20."""
    book = pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    doc = fitz.open(str(pdf_path))
    start, end = parse_pages(pages)
    print(f"{book} — {len(doc)} pages, OCR via {MODEL}, doing {start}-{end or len(doc)}\n")
    print(f"{'pg':>3} {'lines':>6} {'tokens':>7} {'chars':>6} {'diac':>6} {'fixed':>6}  path")
    print("-" * 60)

    summary = {"done": 0, "failed": 0, "stopped_on_quota": False}
    for i, page in enumerate(doc, start=1):
        if i < start:
            continue
        if end and i > end:
            break
        rec_path = out_dir / f"p{i:03d}.json"
        if rec_path.exists() and json.loads(rec_path.read_text(encoding="utf-8")).get("status") in (
                "extracted", "diacritised", "spoken"):
            print(f"{i:>3}  already done, skipping")
            continue

        try:
            jpeg, size = render_page(page)
            (out_dir / "pages" / f"p{i:03d}.jpg").write_bytes(jpeg)
            lines = ocr_page(client, jpeg)
        except Exception as e:
            if failures.handle(out_dir, i, "ocr", e, f"{i:>3}") == "quota":
                summary["stopped_on_quota"] = True
                break
            summary["failed"] += 1
            continue

        tokens = lines_to_tokens(lines, i, clean_word)
        text = " ".join(t["txt"] for t in tokens)
        ratio = diacritic_ratio(text)
        fixed = sum(1 for t in tokens if t["fixes"])
        rec = {
            "book": book, "source_pdf": pdf_path.name, "pdf_sha256_16": pdf_hash,
            "page": i, "source": "ocr", "ocr_engine": MODEL,
            "box_level": "line",          # word boxes are subdivided, not measured
            "render_size": list(size),
            "diac_ratio": round(ratio, 3),
            "path": "keep" if ratio >= DIAC_THRESHOLD else "diacritise",
            "status": "extracted", "tokens": tokens,
        }
        rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        failures.clear(out_dir, i, "ocr")
        summary["done"] += 1
        print(f"{i:>3} {len(lines):>6} {len(tokens):>7} {len(text):>6} {ratio:>6.2f} {fixed:>6}  {rec['path']}")
    doc.close()
    print(f"\n{summary['done']} extracted, {summary['failed']} failed"
          + (" -- stopped on quota" if summary["stopped_on_quota"] else ""))
    return summary


def main():
    book = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
    run(Path(f"assets/PDF Samples/{book}.pdf"), Path(f"work/{book.strip()}"),
        pages=os.environ.get("PAGES", "0"))


if __name__ == "__main__":
    main()
