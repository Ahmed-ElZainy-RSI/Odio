"""Single entry point: run one PDF through the whole pipeline.

Detects whether the PDF carries an embedded text layer (free, exact, no OCR
needed) or is genuinely scanned (needs OCR), then runs
extract -> diacritise -> TTS+align -> reader, writing everything to
work/<book>/.

Usage:
    python3 src/pipeline/main.py "assets/PDF Samples/النجوم.pdf"
    python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --ocr lighton --tts gemini
    python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --pages 5
    python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --pages 13:20
    python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --force-ocr
    python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --trust-text-layer

Every stage is independently resumable (each run_*.py module skips pages
already done), so re-running this after a quota stall picks up where it left
off instead of redoing the book.

--pages only scopes extraction/OCR -- which pages get fetched from the PDF.
Diacritisation and TTS then process everything on disk that still needs it,
regardless of what range was used to fetch it, so a run also catches up any
earlier pages left un-diacritised or unsynthesised instead of hiding that
backlog behind a filter. Their own status checks already prevent redundant
work, so there's nothing for a second range filter to usefully add there.

--force-ocr exists because "has a text layer" and "the text layer is
trustworthy" are not the same thing. Found on شكرا يامى: PyMuPDF reported a
real, >50-word text layer, but it was stale ghost text unrelated to the
page's actual visual content (confirmed by rendering pages and comparing --
page 2's real text is about a character named فرح; the embedded text layer
said آيات and monkeys instead, and page 3's real text is a different
unrelated sentence again). No word-count or diacritic-ratio check catches
this -- the extracted words are real, correctly-spelled Arabic, just for the
wrong page. OCR reads the rendered pixels instead, so it isn't fooled by
this class of defect.

Since spec 012 that check is automatic: a text layer is spot-checked against OCR on up to
2 pages first (text_layer_trust.py) and the book goes down the OCR path if they disagree.
--trust-text-layer skips the check; --force-ocr still forces OCR outright.
"""
import argparse
import os
import sys
from pathlib import Path

import pymupdf
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from pipeline import (
    books,
    build_reader,
    failures,
    run_extract,
    run_ocr_gemini,
    run_ocr_lighton,
    run_speak_gemini,
    run_speak_hume,
    run_tashkel,
    text_layer_trust,
    usage,
)

TEXT_LAYER_MIN_WORDS = 50  # a scanned page's stray OCR-able text stays under this


def has_text_layer(pdf_path: Path, threshold: int = TEXT_LAYER_MIN_WORDS) -> bool:
    doc = pymupdf.open(str(pdf_path))
    words = sum(len(page.get_text("words")) for page in doc)
    doc.close()
    return words > threshold


def _ocr_sampler(ocr: str):
    """The paid OCR used to spot-check a text layer: the same engine as --ocr."""
    if ocr == "gemini":
        from google import genai
        return text_layer_trust.gemini_ocr_words(genai.Client(api_key=os.environ["GEMINI_API_KEY"]))
    return text_layer_trust.lighton_ocr_words(os.environ["LIGHTON_API_KEY"])


def text_layer_is_trusted(pdf_path: Path, work_dir: Path, ocr: str) -> tuple[bool, str]:
    """Spot-check the text layer against OCR (spec 012). Returns (trusted, reason).

    If the check itself can't run (no key, quota), keep today's behaviour -- use the text
    layer -- and say so loudly; blocking every text-layer book on an OCR outage would be
    worse than the rare ghost layer it guards against. A bug still raises (spec 007).
    """
    try:
        with usage.recording(work_dir, "trust-check"):
            result = text_layer_trust.assess(
                pdf_path, ocr_words=_ocr_sampler(ocr), engine=ocr,
                cache_dir=work_dir.parent / ".trust")
    except failures.PROGRAMMING_ERRORS:
        raise
    except Exception as e:  # noqa: BLE001 -- any outage falls back loudly; bugs re-raised above
        return True, (f"WARNING: text-layer check unavailable ({type(e).__name__}: "
                      f"{str(e)[:80]}) -- using the text layer UNCHECKED")
    return result.trusted, result.reason


def run_pipeline(pdf_path: Path, ocr: str = "gemini", tts: str = "gemini",
                  voice: str | None = None, pages="0", force_ocr: bool = False,
                  work_dir: Path | None = None, trust_text_layer: bool = False) -> Path:
    """work_dir defaults to the directory this PDF's *content* maps to (books.py, spec
    013): a renamed or duplicated PDF resumes its existing work instead of starting over."""
    book = pdf_path.stem.strip()
    work_dir = work_dir or books.resolve_work_dir(pdf_path)

    print(f"=== {book} ===")
    use_text_layer, reason = False, "--force-ocr" if force_ocr else "no text layer"
    if not force_ocr and has_text_layer(pdf_path):
        if trust_text_layer:
            use_text_layer, reason = True, "--trust-text-layer"
        else:
            use_text_layer, reason = text_layer_is_trusted(pdf_path, work_dir, ocr)
    if use_text_layer:
        print(f"embedded text layer -> extract.py (no OCR needed) -- {reason}\n")
        run_extract.run(pdf_path, work_dir, pages=pages)
    else:
        print(f"OCR path ({ocr}) -- {reason}\n")
        if ocr == "gemini":
            run_ocr_gemini.run(pdf_path, work_dir, pages=pages)
        else:
            run_ocr_lighton.run(pdf_path, work_dir, pages=pages)

    print("\n--- diacritising ---")
    run_tashkel.run(work_dir)

    print(f"\n--- synthesising ({tts}) ---")
    if tts == "hume":
        run_speak_hume.run(work_dir, voice or run_speak_hume.VOICE)
    else:
        run_speak_gemini.run(work_dir, voice or run_speak_gemini.VOICE)

    print("\n--- building reader ---")
    out = build_reader.build(work_dir)
    if out:
        print(f"\ndone -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Run one PDF through extract -> diacritise -> TTS+align -> reader.")
    ap.add_argument("pdf", help="path to the book PDF")
    ap.add_argument("--ocr", choices=["gemini", "lighton"], default="gemini",
                     help="OCR engine for scanned PDFs (ignored if the PDF has a text layer); "
                          "gemini gives line-level boxes, lighton gives none")
    ap.add_argument("--tts", choices=["gemini", "hume"], default="gemini",
                     help="TTS engine (default: gemini). gemini needs faster-whisper for "
                          "alignment (no native timestamps on the free API tier) but has "
                          "quota available; hume gives native timestamps but its credit "
                          "balance is currently exhausted")
    ap.add_argument("--voice", default=None, help="override the default voice for the chosen TTS engine")
    ap.add_argument("--pages", default="0",
                     help="limit which pages get fetched during extraction/OCR (0 = all): "
                          "'5' = pages 1-5, '13:20' = pages 13-20, '13:' = page 13 to the end -- "
                          "diacritisation and synthesis are unaffected and process everything "
                          "on disk that still needs it, so they also catch up any backlog")
    ap.add_argument("--force-ocr", action="store_true",
                     help="use the OCR path even if the PDF has a text layer -- for PDFs where "
                          "the text layer exists but is wrong (e.g. stale text unrelated to the "
                          "page's actual visual content, which no automatic check catches)")
    ap.add_argument("--trust-text-layer", action="store_true",
                     help="skip the text-layer spot check (up to 2 OCR calls per text-layer "
                          "book) and use the embedded text as-is")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"no such file: {pdf_path}")

    run_pipeline(pdf_path, ocr=args.ocr, tts=args.tts, voice=args.voice, pages=args.pages,
                 force_ocr=args.force_ocr, trust_text_layer=args.trust_text_layer)


if __name__ == "__main__":
    main()
