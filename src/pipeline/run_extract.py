"""Layers 01-04 for one book: extract -> tokenise -> clean -> route by tashkeel."""
import json, sys, hashlib
from pathlib import Path
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.extract import extract_page, diacritic_ratio

BOOK = "النجوم"
PDF = Path(f"assets/PDF Samples/{BOOK}.pdf")
OUT = Path(f"work/{BOOK}")
DIAC_THRESHOLD = 0.15

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pdf_hash = hashlib.sha256(PDF.read_bytes()).hexdigest()[:16]
    doc = fitz.open(str(PDF))
    summary = []
    for i, page in enumerate(doc, start=1):
        tokens = extract_page(page, i, i)
        text = " ".join(t["txt"] for t in tokens)
        ratio = diacritic_ratio(text)
        rec = {
            "book": BOOK,
            "source_pdf": PDF.name,
            "pdf_sha256_16": pdf_hash,      # provenance: not the filename
            "page": i,
            "source": "pdf-text-layer",
            "diac_ratio": round(ratio, 3),
            "path": "keep" if ratio >= DIAC_THRESHOLD else "diacritise",
            "status": "extracted",
            "tokens": tokens,
        }
        (OUT / f"p{i:03d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        fixed = sum(1 for t in tokens if t["fixes"])
        summary.append((i, len(tokens), len(text), ratio, rec["path"], fixed))
    doc.close()

    print(f"{BOOK} — sha256:{pdf_hash}\n")
    print(f"{'pg':>3} {'tokens':>7} {'chars':>6} {'diac':>6} {'path':>11} {'fixed':>6}")
    print("-"*48)
    for r in summary:
        print(f"{r[0]:>3} {r[1]:>7} {r[2]:>6} {r[3]:>6.2f} {r[4]:>11} {r[5]:>6}")
    tot_t=sum(r[1] for r in summary); tot_c=sum(r[2] for r in summary); tot_f=sum(r[5] for r in summary)
    print("-"*48)
    print(f"{'':3} {tot_t:>7} {tot_c:>6} {'':6} {'':11} {tot_f:>6}")
    print(f"\n{len(summary)} page records -> {OUT}/")
    print(f"{tot_f} of {tot_t} tokens ({100*tot_f/tot_t:.1f}%) required a cleanup rule")

if __name__ == "__main__":
    main()
