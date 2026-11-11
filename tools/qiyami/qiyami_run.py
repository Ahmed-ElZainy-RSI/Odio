"""OCR + tashkeel for one «قيمي» book with Gemini-3.8-Flash (setup chosen in tune_flash.py). Spec 035.

    python tools/qiyami/qiyami_run.py --pdf "books/الأنانية.pdf"
    python tools/qiyami/qiyami_run.py --pdf "books/التعاون (1).pdf" --book التعاون
    python tools/qiyami/qiyami_run.py --pdf ... --pages 4 5       # file page numbers (1-based)
    python tools/qiyami/qiyami_run.py --pdf ... --samples 1       # cheaper single-call setup
    python tools/qiyami/qiyami_run.py --pdf ... --rebuild         # no API calls: rebuild result/ from saved runs

Output (one folder per book, nothing shared between books):
    result/<book>/images/p001.png      page rendered from the PDF at --dpi (what Gemini saw)
    result/<book>/json/p001.json       text, lines, words, settings, score when a reference exists
    result/<book>/book.json            book metadata + per-page summary
    result/<book>/<book>_كامل.txt      whole book as plain text
Raw model answers: work/qiyami/<book>/page_N/sample_*.raw (git-ignored).
Needs $GEMINI_API_KEY unless --rebuild. Every call uploads the page to Google.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import pymupdf

import score_ocr as S
import tune_flash as T

REPO = Path(__file__).resolve().parents[2]
MODEL = "gemini-3.8-flash"
SETUP = {"prompt": "v2", "media_resolution": "high", "thinking": "low", "zoom_strips": 3,
         "strip_scale": 2.0}
MARKS = re.compile(f"[{S.MARKS}]")
LETTERS = re.compile(r"[ء-ي]")
# hand-checked reference text exists only for these pages (score_ocr.GT)
SCORED = {"الأمانة": ("page_4", "page_5")}


def page_json(book, pdf, sha, n, text, img, dpi, samples, score):
    letters = len(LETTERS.findall(text))
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    words = text.split()
    return {
        "book": book, "source_pdf": pdf.name, "pdf_sha256_16": sha, "page": n,
        "source": "ocr", "ocr_engine": MODEL,
        "setup": {**SETUP, "samples": samples, "vote": samples > 1, "render_dpi": dpi},
        "render_size": list(img.size) if img else None,
        "image": f"images/p{n:03d}.png",
        "diac_ratio": round(len(MARKS.findall(text)) / letters, 3) if letters else 0,
        "text": text, "lines": lines,
        "tokens": [{"id": f"p{n:03d}w{i:03d}", "txt": w} for i, w in enumerate(words)],
        "score": score,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--book", help="output folder name (default: PDF file name)")
    ap.add_argument("--pages", nargs="+", type=int)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--out", default=str(REPO / "result"))
    ap.add_argument("--runs", default=str(REPO / "work" / "qiyami"), help="where raw model answers are kept")
    ap.add_argument("--rebuild", action="store_true", help="rebuild result/ from saved runs, no API calls")
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not a.rebuild and not key:
        sys.exit("needs $GEMINI_API_KEY")

    from PIL import Image
    pdf = Path(a.pdf)
    book = a.book or pdf.stem.strip()
    doc = pymupdf.open(pdf)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    root = Path(a.out) / book
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "json").mkdir(exist_ok=True)
    T.RUNS = Path(a.runs)
    runs = T.RUNS / book
    args = argparse.Namespace(variant=book, model=MODEL, prompt="v2", res="high", thinking="low",
                              zoom=3, scale=2.0, samples=a.samples, temperature=0.0,
                              enhance=False, review=False)

    for n in a.pages or range(1, len(doc) + 1):
        name = f"page_{n}"
        img_path = root / "images" / f"p{n:03d}.png"
        if not img_path.exists() or not a.rebuild:
            doc[n - 1].get_pixmap(dpi=a.dpi).save(img_path)
        if not a.rebuild:
            T.PAGES[name] = str(img_path.resolve())
            S.GT.setdefault(name, "ا")  # run_page needs a reference; placeholder score is discarded
            T.run_page(name, args, key)
        out_txt = runs / name / "Gemini-3.8-Flash.txt"
        if not out_txt.exists():
            print(f"p{n:03d}: no saved run, skipped"); continue
        text = out_txt.read_text(encoding="utf-8").strip()
        samples = len(list((runs / name).glob("sample_*.raw")))
        score = None
        if name in SCORED.get(book, ()):
            c, w, t, ad, fa = S.score(text, S.GT[name])
            score = {"letters": round(c, 4), "words": round(w, 4), "tashkeel": round(t, 4),
                     "reference": "score_ocr.GT"}
        with Image.open(img_path) as img:
            data = page_json(book, pdf, sha, n, text, img, a.dpi, samples, score)
        (root / "json" / f"p{n:03d}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
        print(f"p{n:03d}: {len(data['tokens'])} words" + (f"  tashkeel {score['tashkeel']:.1%}" if score else ""))

    pages = sorted((root / "json").glob("p*.json"))
    summary = [json.loads(p.read_text(encoding="utf-8")) for p in pages]
    (root / "book.json").write_text(json.dumps({
        "id": sha, "title": book, "source_pdf": pdf.name, "pages": len(doc),
        "ocr_engine": MODEL, "setup": SETUP, "date": date.today().isoformat(),
        "page_summary": [{"page": d["page"], "words": len(d["tokens"]), "diac_ratio": d["diac_ratio"],
                          "score": d["score"]} for d in summary],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / f"{book}_كامل.txt").write_text(
        "\n\n".join(f"===== صفحة {d['page']} =====\n{d['text']}" for d in summary), encoding="utf-8")
    print(f"\nWrote {root.resolve()}")


if __name__ == "__main__":
    main()
