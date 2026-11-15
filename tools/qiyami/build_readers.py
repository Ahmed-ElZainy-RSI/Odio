"""Each «قيمي» book as the pipeline's book folder, up to OCR approval: result/<book>/ laid out like a
pipeline run's work/<book>/ (e.g. result/دبدوب الطماع), without audio. No API calls.

    python tools/qiyami/build_readers.py                 # every book read by qiyami_run.py
    python tools/qiyami/build_readers.py --book الثقة    # one book

Reads the OCR stage's output in work/qiyami/<book>/ (qiyami_run.py: images/, json/, book.json, raw runs,
timings.jsonl, usage.jsonl) and writes result/<book>/, nothing else in it:
    pages/pNNN.jpg    the page image the OCR read
    pNNN.json         page records, the pipeline's fields: status "extracted", path "keep" (the OCR's own marks
                      are spoken), tokens with word boxes; a hand-fixed word carries evidence (original + why)
    book.json         id / title / variant / sources
    timings.jsonl     wall clock per page and step (when the OCR run was timed)
    usage.jsonl       every paid Gemini call (when the OCR run recorded it)
    check_pages/      high-resolution renders for the hand check (tools/build_ocr_check.py)
    ocr_check.html    hand check: every word beside its crop; its Export gives ocr_check_<book>.json
    reader.html       src/pipeline/build_reader.py in preview mode: page image beside the text, no audio
audio/ and status "spoken" come after the OCR is approved.

Word boxes, the pipeline's way (src/pipeline/ocr_gemini.lines_to_tokens + ink_boxes.fit_boxes): one box per
printed line, split by letter count, then fitted to the ink. The line boxes come from the PDF's text layer
(a line's position is reliable even where its word boxes are not); each OCR line is matched to the layer
line holding the same words. A line with no layer match (no text layer, a vector title) gets no boxes.
"""
import argparse
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))
from pipeline import build_reader  # noqa: E402
from pipeline.ink_boxes import fit_boxes  # noqa: E402
import build_ocr_check  # noqa: E402
import check_textlayer as C  # noqa: E402

PUNCT = re.compile(r"[^\wً-ٰ]")


def bare(w: str) -> str:
    return PUNCT.sub("", w)


def evidence(n: int, words: list[str], raw: str | None, fixes: list[dict], date: str) -> dict[int, dict]:
    """word index -> {raw, evidence} for words that differ from the raw OCR (voted text before hand fixes)."""
    out = {}
    if not raw:
        return out
    rw = raw.split()
    for op, i1, i2, j1, j2 in SequenceMatcher(None, rw, words, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        for k, j in enumerate(range(j1, j2)):
            orig = rw[i1 + k] if i1 + k < i2 else " ".join(rw[i1:i2])
            why = next((f["why"] for f in fixes if bare(words[j]) and bare(words[j]) in
                        {bare(x) for x in f["new"].split()}), "hand fix")
            out[j] = {"raw": orig, "evidence": [{"original": orig, "detector": "text-layer check",
                                                 "decided_by": "hand, on the page image", "rule": why, "at": date}]}
    return out


def layer_lines(page) -> list[tuple[list[float], Counter]]:
    """Visual lines of the text layer: (box in % of the page [x, y, w, h], word keys). Runs on one
    baseline are merged; running headers/footers («|») are left out."""
    W, H = page.rect.width, page.rect.height
    runs = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            text = "".join(s["text"] for s in l["spans"])
            if "|" in text or not text.strip():
                continue
            keys = [C.key(w) for w in C.words(re.sub(f"(?<![ء-يٱ{C.MARKS}ـ]){C.M}+", "", text))]
            if keys:
                runs.append([list(l["bbox"]), keys])
    runs.sort(key=lambda r: (r[0][1] + r[0][3]) / 2)
    merged = []
    for box, keys in runs:
        cy = (box[1] + box[3]) / 2
        if merged and abs(cy - merged[-1][2]) < (box[3] - box[1]) * 0.5:
            m = merged[-1]
            m[0] = [min(m[0][0], box[0]), min(m[0][1], box[1]), max(m[0][2], box[2]), max(m[0][3], box[3])]
            m[1] += keys
        else:
            merged.append([box, keys, cy])
    return [([100 * b[0] / W, 100 * b[1] / H, 100 * (b[2] - b[0]) / W, 100 * (b[3] - b[1]) / H], Counter(k))
            for b, k, _ in merged]


def word_boxes(page, lines: list[str], image: Path) -> list[list[float] | None]:
    """One box per word of the page text (text.split() order), or None where no line box was found."""
    layer = layer_lines(page) if page is not None else []
    used, boxes, toks = set(), [], []
    for line in lines:
        words = line.split()
        keys = Counter(C.key(C.to_layer_form(w)) for w in C.words(line))
        best, score = None, 0.0
        for i, (box, lk) in enumerate(layer):
            if i in used or not keys:
                continue
            s = sum((keys & lk).values()) / max(sum(keys.values()), sum(lk.values()))
            if s > score:
                best, score = i, s
        if best is None or score < 0.5:
            boxes += [None] * len(words)
            continue
        used.add(best)
        x0, y0, w, h = layer[best][0]
        weights = [max(1, len(C.skel(bare(x)))) for x in words]
        acc = 0.0
        for k, wt in enumerate(weights):  # right to left: first word takes the rightmost slice
            share = w * wt / sum(weights)
            box = [round(x0 + w - acc - share, 3), round(y0, 3), round(share, 3), round(h, 3)]
            acc += share
            toks.append({"id": f"w{len(boxes)}", "txt": words[k], "box": box})
            boxes.append(box)
    if toks and image.exists():
        fitted = fit_boxes(toks, np.asarray(Image.open(image).convert("L")))
        for t in toks:
            if t["id"] in fitted:
                boxes[int(t["id"][1:])] = fitted[t["id"]]
    return boxes


def build_book(runs: Path, result: Path = REPO / "result") -> Path:
    """runs = work/qiyami/<book> (OCR stage output). Writes result/<book>/ and returns it."""
    book = runs.name
    meta = json.loads((runs / "book.json").read_text(encoding="utf-8"))
    pdf_path = next((p for p in (REPO / "books").glob("*.pdf") if p.name == meta["source_pdf"]), None)
    doc = pymupdf.open(pdf_path) if pdf_path else None
    out = result / book
    (out / "pages").mkdir(parents=True, exist_ok=True)
    for old in out.glob("p[0-9][0-9][0-9].json"):
        old.unlink()
    boxed = total = 0
    for f in sorted((runs / "json").glob("p*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        n = d["page"]
        words = d["text"].split()
        raw_f = runs / f"page_{n}" / "Gemini-3.8-Flash.txt"
        raw = raw_f.read_text(encoding="utf-8") if raw_f.exists() and d.get("corrections") else None
        ev = evidence(n, words, raw, d.get("corrections", []), meta["date"])
        page = doc[n - 1] if doc else None
        has_layer = page is not None and bool(page.get_text().strip())
        png = runs / d["image"]
        bxs = word_boxes(page if has_layer else None, d["lines"], png)
        with Image.open(png) as im:  # pages/pNNN.jpg: what the OCR read, as the pipeline keeps it
            im.convert("RGB").save(out / "pages" / f"p{n:03d}.jpg", "JPEG", quality=90)
        tokens = []
        for i, w in enumerate(words):
            t = {"id": f"p{n:03d}w{i:03d}", "raw": ev.get(i, {}).get("raw", w), "txt": w,
                 "fixes": ["hand-checked tashkeel"] if i in ev else []}
            if bxs[i] is not None:
                t["box"] = bxs[i]
            if i in ev:
                t["evidence"] = ev[i]["evidence"]
            tokens.append(t)
        n_box = sum(b is not None for b in bxs)
        boxed += n_box; total += len(words)
        rec = {"book": book, "source_pdf": meta["source_pdf"], "pdf_sha256_16": meta["id"], "page": n,
               "source": "ocr", "ocr_engine": meta["ocr_engine"],
               "box_level": "word" if words and n_box == len(words) else "none",
               "render_size": d["render_size"], "diac_ratio": d["diac_ratio"], "path": "keep",
               "status": "extracted", "tokens": tokens, "box_fit": {"fitted": n_box, "total": len(words)}}
        (out / f"p{n:03d}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "book.json").write_text(json.dumps({
        "id": meta["id"], "title": book, "variant": "", "sources": [f"books/{meta['source_pdf']}"]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("timings.jsonl", "usage.jsonl"):
        if (runs / name).exists():
            (out / name).write_text((runs / name).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{book}: {boxed}/{total} words boxed")
    if pdf_path:
        build_ocr_check.build(out, pdf_path)
    build_reader.build(out, preview=True)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(REPO / "work" / "qiyami"), help="qiyami_run.py's output root")
    ap.add_argument("--out", default=str(REPO / "result"))
    ap.add_argument("--book")
    a = ap.parse_args()
    root = Path(a.runs)
    dirs = [root / a.book] if a.book else sorted(d for d in root.iterdir() if (d / "json").is_dir())
    for d in dirs:
        build_book(d, Path(a.out))


if __name__ == "__main__":
    main()
