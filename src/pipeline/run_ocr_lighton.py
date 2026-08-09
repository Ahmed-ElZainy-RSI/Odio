"""Layers 01-04 for a scanned book via LightOn. Resumable per page."""
import json, os, sys, hashlib
from pathlib import Path
from pypdf import PdfReader
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.extract import clean_word, diacritic_ratio
from pipeline.ocr_lighton import (slice_page, parse_page, clean_markdown,
                                  text_to_tokens, ENGINE)

load_dotenv()
BOOK = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
PDF = Path(f"assets/PDF Samples/{BOOK}.pdf")
OUT = Path(f"work/{BOOK.strip()}-lighton")
DIAC_THRESHOLD = 0.15
LIMIT = int(os.environ.get("PAGES", "0"))


def main():
    key = os.environ["LIGHTON_API_KEY"]
    OUT.mkdir(parents=True, exist_ok=True)
    pdf_hash = hashlib.sha256(PDF.read_bytes()).hexdigest()[:16]
    reader = PdfReader(str(PDF))
    n = LIMIT or len(reader.pages)
    print(f"{BOOK.strip()} — {len(reader.pages)} pages via {ENGINE}, doing {n}")
    print("box_level=none: no geometry from this engine, so no page-image view\n")
    print(f"{'pg':>3} {'tokens':>7} {'chars':>6} {'diac':>6} {'fixed':>6}  path")
    print("-" * 52)

    for i in range(1, n + 1):
        rec_path = OUT / f"p{i:03d}.json"
        if rec_path.exists() and json.loads(rec_path.read_text(encoding="utf-8")).get("status") == "extracted":
            print(f"{i:>3}  already done, skipping")
            continue
        try:
            md = parse_page(slice_page(reader, i - 1), key, f"p{i:03d}.pdf")
        except Exception as e:
            print(f"{i:>3}  FAILED: {str(e)[:120]}")
            print("     stopping — rerun to resume from this page")
            break

        text = clean_markdown(md)
        tokens = text_to_tokens(text, i, clean_word)
        joined = " ".join(t["txt"] for t in tokens)
        ratio = diacritic_ratio(joined)
        fixed = sum(1 for t in tokens if t["fixes"])
        rec = {
            "book": BOOK.strip(), "source_pdf": PDF.name, "pdf_sha256_16": pdf_hash,
            "page": i, "source": "ocr", "ocr_engine": ENGINE,
            "box_level": "none",
            # ratio is a routing input only; on this engine it is NOT a quality
            # signal — LightOn invents diacritics over misread letters
            "diac_ratio": round(ratio, 3),
            "path": "keep" if ratio >= DIAC_THRESHOLD else "diacritise",
            "status": "extracted", "tokens": tokens,
        }
        rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{i:>3} {len(tokens):>7} {len(joined):>6} {ratio:>6.2f} {fixed:>6}  {rec['path']}")

if __name__ == "__main__":
    main()
