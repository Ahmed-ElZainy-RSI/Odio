"""Layers 01-04 for one book: extract -> tokenise -> clean -> route by tashkeel.

Resumable: a page whose record already exists is skipped entirely, not just
re-read. Extraction is deterministic (same PDF text layer in, same tokens
out), so once a page has a record every later stage may have built on top of
it -- tashkeel, TTS, bind timings -- and unconditionally regenerating tokens
would wipe all of that even though the underlying text hasn't changed. That
happened for real: running this against an already-fully-spoken book reset
every page back to status 'extracted', erasing 'spoken' status, the audio
path, and Hume's persisted word list from all 15 narrated pages, even though
the audio files themselves were untouched on disk.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.extract import extract_page, diacritic_ratio
from pipeline.records import write_record
from pipeline.ocr_gemini import render_page
from pipeline.pages import parse_pages

DIAC_THRESHOLD = 0.15


def run(pdf_path: Path, out_dir: Path, pages="0"):
    """Extract pdf_path's embedded text layer into out_dir.

    pages: "0"/""/None = all, "5" = pages 1-5, "13:20" = pages 13-20 (see pages.py).
    """
    book = pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
    doc = pymupdf.open(str(pdf_path))
    total = len(doc)
    start, end = parse_pages(pages)
    summary = []
    for i, page in enumerate(doc, start=1):
        if i < start:
            continue
        if end and i > end:
            break
        rec_path = out_dir / f"p{i:03d}.json"
        img_path = out_dir / "pages" / f"p{i:03d}.jpg"
        if not img_path.exists():
            # word boxes are useless for the reader's comparison view without
            # something to draw them on -- render once, same as the OCR path
            jpeg, _ = render_page(page)
            img_path.write_bytes(jpeg)
        if rec_path.exists():
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            text = " ".join(t["txt"] for t in rec["tokens"])
            fixed = sum(1 for t in rec["tokens"] if t["fixes"])
            summary.append((i, len(rec["tokens"]), len(text), rec["diac_ratio"], "skipped", fixed))
            continue
        tokens = extract_page(page, i, i)
        text = " ".join(t["txt"] for t in tokens)
        ratio = diacritic_ratio(text)
        rec = {
            "book": book,
            "source_pdf": pdf_path.name,
            "pdf_sha256_16": pdf_hash,      # provenance: not the filename
            "page": i,
            "source": "pdf-text-layer",
            "diac_ratio": round(ratio, 3),
            "path": "keep" if ratio >= DIAC_THRESHOLD else "diacritise",
            "status": "extracted",
            "tokens": tokens,
        }
        write_record(out_dir / f"p{i:03d}.json", rec)
        fixed = sum(1 for t in tokens if t["fixes"])
        summary.append((i, len(tokens), len(text), ratio, rec["path"], fixed))
    doc.close()

    print(f"{book} — sha256:{pdf_hash}, {total} pages, doing {start}-{end or total}\n")
    print(f"{'pg':>3} {'tokens':>7} {'chars':>6} {'diac':>6} {'path':>11} {'fixed':>6}")
    print("-" * 48)
    for r in summary:
        print(f"{r[0]:>3} {r[1]:>7} {r[2]:>6} {r[3]:>6.2f} {r[4]:>11} {r[5]:>6}")
    tot_t = sum(r[1] for r in summary); tot_c = sum(r[2] for r in summary); tot_f = sum(r[5] for r in summary)
    print("-" * 48)
    print(f"{'':3} {tot_t:>7} {tot_c:>6} {'':6} {'':11} {tot_f:>6}")
    print(f"\n{len(summary)} page records -> {out_dir}/")
    if tot_t:
        print(f"{tot_f} of {tot_t} tokens ({100*tot_f/tot_t:.1f}%) required a cleanup rule")
    return summary


def main():
    book = os.environ.get("BOOK", "النجوم")
    run(Path(f"assets/PDF Samples/{book}.pdf"), Path(f"work/{book}"),
        pages=os.environ.get("PAGES", "0"))


if __name__ == "__main__":
    main()
