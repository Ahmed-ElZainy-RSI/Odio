"""Is this PDF's text layer about the page it's on? (spec 012)

"Has a text layer" and "the text layer is right" are different things. On شكرا يامى the
embedded text was real, well-spelled Arabic -- for a different page (ghost text from an
earlier version of the file). No word-count or diacritic check can see that; only
comparing it with what the page actually shows can.

So before trusting a text layer, OCR a small sample of its pages (default 2: the first
page with enough words and the middle one) and compare. Below the threshold, the whole
book goes down the OCR path, with the reason printed. Cost: at most `sample` OCR calls per
text-layer book, cached by PDF content so a re-run costs nothing.

The comparison folds what legitimately differs between a text layer and OCR of the same
words -- diacritics, tatweel, punctuation, alef forms, ta marbuta, alef maqsura -- and is
order-aware (difflib), so the right words in a scrambled order still score low.
"""
import json
import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from pipeline.books import book_id
from pipeline.extract import clean_word, extract_page

DEFAULT_THRESHOLD = float(os.environ.get("TEXT_LAYER_MIN_SIMILARITY", "0.65"))
MIN_SAMPLE_WORDS = 20

_MARKS = re.compile("[\u0610-\u061a\u064b-\u0652\u0670\u06d6-\u06ed\u0640]")
_ALEF = re.compile("[\u0622\u0623\u0625\u0671]")

OcrWords = Callable[[Path, int], list[str]]


def normalize(word: str) -> str:
    w = _MARKS.sub("", unicodedata.normalize("NFC", word))
    w = _ALEF.sub("\u0627", w).replace("\u0629", "\u0647").replace("\u0649", "\u064a")
    return "".join(c for c in w if unicodedata.category(c)[0] in "LN")


def similarity(a: list[str], b: list[str]) -> float:
    """0..1, order-aware agreement between two word sequences after folding."""
    na = [w for w in map(normalize, a) if w]
    nb = [w for w in map(normalize, b) if w]
    if not na and not nb:
        return 1.0
    return SequenceMatcher(a=na, b=nb, autojunk=False).ratio()


def text_layer_words(pdf_path: Path, page_number: int) -> list[str]:
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[page_number - 1]
        return [t["txt"] for t in extract_page(page, page_number, page_number - 1)]
    finally:
        doc.close()


def sample_pages(pdf_path: Path, k: int = 2, min_words: int = MIN_SAMPLE_WORDS) -> list[int]:
    """The first page with enough text-layer words, then the middle one, ... up to k."""
    doc = pymupdf.open(str(pdf_path))
    try:
        rich = [i + 1 for i, p in enumerate(doc) if len(p.get_text("words")) >= min_words]
    finally:
        doc.close()
    if not rich:
        return []
    picks = [rich[0], rich[len(rich) // 2], rich[-1]]
    out: list[int] = []
    for p in picks:
        if p not in out:
            out.append(p)
    return out[:k]


@dataclass
class TrustResult:
    trusted: bool
    similarity: float | None
    threshold: float
    pages: dict[int, float] = field(default_factory=dict)
    reason: str = ""


def assess(pdf_path: Path, *, ocr_words: OcrWords, engine: str,
           threshold: float = DEFAULT_THRESHOLD, sample: int = 2,
           cache_dir: Path | None = None) -> TrustResult:
    """Compare the text layer with OCR on a few pages. Per-page scores are cached by
    (PDF content, engine), so a re-run -- or a threshold change -- costs no OCR."""
    pdf_path = Path(pdf_path)
    pages = sample_pages(pdf_path, k=sample)
    if not pages:
        return TrustResult(False, None, threshold, reason="no page has enough text-layer words")

    cache_path = cache_dir / f"{book_id(pdf_path)}-{engine}.json" if cache_dir else None
    cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.exists() else {}
    scores: dict[int, float] = {}
    for p in pages:
        if str(p) in cached:
            scores[p] = cached[str(p)]
            continue
        scores[p] = round(similarity(text_layer_words(pdf_path, p), ocr_words(pdf_path, p)), 3)
        cached[str(p)] = scores[p]
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cached, indent=1), encoding="utf-8")

    worst = min(scores.values())
    trusted = worst >= threshold
    detail = ", ".join(f"p{p}={s:.2f}" for p, s in sorted(scores.items()))
    reason = (f"text layer matches OCR ({detail})" if trusted else
              f"text-layer-mismatch: text layer disagrees with what the page shows ({detail} "
              f"< {threshold:.2f})")
    return TrustResult(trusted, worst, threshold, scores, reason)


# ── OCR samplers (paid) ─────────────────────────────────────────────────────────


def gemini_ocr_words(client) -> OcrWords:
    from pipeline.ocr_gemini import lines_to_tokens, ocr_page, render_page

    def words(pdf_path: Path, page_number: int) -> list[str]:
        doc = pymupdf.open(str(pdf_path))
        try:
            jpeg, _size = render_page(doc[page_number - 1])
        finally:
            doc.close()
        lines = ocr_page(client, jpeg)
        return [t["txt"] for t in lines_to_tokens(lines, page_number, clean_word)]

    return words


def lighton_ocr_words(api_key: str) -> OcrWords:
    from pypdf import PdfReader

    from pipeline.ocr_lighton import (
        clean_markdown,
        parse_page,
        slice_page,
        text_to_tokens,
    )

    def words(pdf_path: Path, page_number: int) -> list[str]:
        md = parse_page(slice_page(PdfReader(str(pdf_path)), page_number - 1), api_key,
                        f"p{page_number:03d}.pdf")
        return [t["txt"] for t in text_to_tokens(clean_markdown(md), page_number, clean_word)]

    return words


def as_dict(result: TrustResult) -> dict:
    return asdict(result)
