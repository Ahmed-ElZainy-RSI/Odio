"""Layer 01+02+03 — extract, tokenise, clean.

For PDFs that carry an embedded text layer (25 of the 36 in assets/), the text
and per-word bounding boxes come straight out of the file: free, exact, and
without the letter-substitution errors that OCR introduces. Measured on this
corpus, OCR turned الْفَصْلِ ("classroom") into الفضل ("grace") four times in one
book; the text layer has it right.

The text layer is not clean, though. It has its own defects, all mechanical:

  kashida       ـ U+0640 inserted for justification (593 in النجوم)
  leading mark  a combining vowel stranded at word start, 7.2% of words
  dropped hamza ال + sukun + vowel, where the أ carrier was lost, 4.4%

These are fixed by rule, not by a model — unlike OCR errors, they have exact
signatures and no ambiguity about intent.
"""
import re
import unicodedata

KASHIDA = "ـ"
SUKUN = "ْ"
VOWELS = "َُِ"          # fatha, damma, kasra
COMBINING = re.compile(r"[ً-ْٰ]")
ARABIC = re.compile(r"[ء-ي]")
LEADING_MARK = re.compile(r"^[ً-ْٰ]+")
# leading punctuation the extractor glued to the next word (،رانيا)
LEADING_PUNCT = re.compile(r"^[^\wء-ي\s]+")

# NFC reorders combining marks by canonical class, so the raw PDF sequence
# sukun(cc=34)+vowel(cc=30) normalises to vowel+sukun. Matching therefore has
# to happen *after* normalisation, against the normalised order:
#   ا ل  <vowel>  sukun  <consonant>
# A vowel sitting directly on the ل, with a sukun after it, is the signature of
# a dropped أ carrier — ال is never itself vowelled in this position.
DROPPED_HAMZA = re.compile("^ال([" + VOWELS + "])" + SUKUN)


def clean_word(raw: str):
    """Return (cleaned, fixes[]). Each fix names the rule that fired."""
    fixes = []
    w = raw

    if KASHIDA in w:
        w = w.replace(KASHIDA, "")
        fixes.append("kashida")

    # Punctuation that the extractor attached to the front of the following
    # word rather than the end of the previous one (،رانيا). Strip it first,
    # otherwise it hides the stranded-mark and dropped-hamza signatures behind
    # it — five tokens in النجوم were masked this way.
    m = LEADING_PUNCT.match(w)
    if m:
        w = w[m.end():]
        fixes.append("leading-punct")

    # A combining mark cannot begin a word — it belongs to a letter the
    # extractor dropped or reordered. Move it off the front.
    m = LEADING_MARK.match(w)
    if m:
        w = w[m.end():]
        fixes.append("leading-mark")

    w = unicodedata.normalize("NFC", w)

    # الَْطْفَال  ->  الأَطْفَال : reinsert the أ that carried the vowel.
    m = DROPPED_HAMZA.match(w)
    if m:
        vowel = m.group(1)
        w = "ال" + "أ" + vowel + w[m.end():]
        fixes.append("dropped-hamza")

    return w, fixes


def is_word(s: str) -> bool:
    return bool(ARABIC.search(s))


def diacritic_ratio(text: str) -> float:
    letters = len(ARABIC.findall(text))
    return len(COMBINING.findall(text)) / letters if letters else 0.0


def extract_page(page, page_no: int, page_index: int):
    """One page -> token records with stable ids and normalised bboxes."""
    W, H = page.rect.width, page.rect.height
    tokens = []
    for w in page.get_text("words"):
        x0, y0, x1, y1, raw = w[0], w[1], w[2], w[3], w[4]
        if not is_word(raw):
            continue                      # page numbers, stray punctuation
        txt, fixes = clean_word(raw)
        if not txt:
            continue
        tokens.append({
            "id": f"p{page_no:03d}w{len(tokens):03d}",
            "raw": raw,
            "txt": txt,
            "fixes": fixes,
            # percentages, so the reader can overlay on any render size
            "box": [round(100 * x0 / W, 3), round(100 * y0 / H, 3),
                    round(100 * (x1 - x0) / W, 3), round(100 * (y1 - y0) / H, 3)],
        })
    return tokens
