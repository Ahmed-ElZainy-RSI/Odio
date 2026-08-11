"""Layer 01, OCR path via LightOn — text only, no bounding boxes.

Chosen because LightOn had credit available when Gemini's quota was exhausted.
The trade-off is explicit and recorded in every page record:

    box_level: "none"   -> the reader must disable the page-image view

Measured against the printed page of المومياوات, LightOn is less faithful than
Gemini was:

    printed        LightOn        Gemini
    العثور         العندوع  ✗     العثور   ✓
    مُستنقعات      مُستنفعات ✗     مُستنقعات ✓
    طَريقتانِ       ظُرِيقَتَانِ ✗   طريقتانِ  ✓
    وتُوجدُ         وُتُوحُدُ  ✗     وتُوجَدُ  ✓

Note the last two: LightOn also *invents* diacritics on top of wrong letters,
which inflates a naive diacritic-ratio check. On page 8 that scored LightOn
0.641 against Gemini's 0.290 while being substantially less correct — so the
tashkeel router's ratio must never be read as a quality signal on this path.

LightOn's /parse caps a request at 15 pages and 20 MB, so books are sliced.

Layout-heavy pages (title pages, tables of contents, styled call-out boxes)
come back as real HTML embedded in the markdown -- <div style="...">,
<table>/<tr>/<td>, <p>, <br>, even LaTeX text wrappers like $\text{...}$ --
not just markdown syntax. Measured on المومياوات, 6 of 33 pages had this:
un-stripped tags leaked through word-splitting as fake tokens ('div', 'td>4',
'style="text-align:'), which would have been spoken aloud verbatim. clean_markdown
strips tags down to their text content rather than just markdown headings and
image syntax, replacing every tag with a newline so cell/paragraph boundaries
don't glue adjacent words together.
"""
import io
import os
import re

import requests
from pypdf import PdfReader, PdfWriter

ENDPOINT = "https://api.lighton.ai/api/v3/parse"
ENGINE = "lighton/parse-v3"

# the OCR service appends commentary about images and bare page numbers
NOTE = re.compile(r"(?m)^Note:.*$")
PAGE_NO = re.compile(r"(?m)^\s*\d+\s*$")
IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HEADING = re.compile(r"(?m)^#{1,6}\s*")
HTML_TAG = re.compile(r"<[^>]+>")
# not just \text{} -- a later page produced $\mathcal{...}$ for a styled badge,
# so this catches any \command{...} wrapper, keeping only the inner text
LATEX_CMD = re.compile(r"\$?\\[a-zA-Z]+\{([^}]*)\}\$?")
STRAY_LATEX = re.compile(r"[$\\]")


def slice_page(reader: PdfReader, index: int) -> bytes:
    """One-page PDF, so each request maps to exactly one page record."""
    w = PdfWriter()
    w.add_page(reader.pages[index])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def parse_page(pdf_bytes: bytes, api_key: str, name="page.pdf"):
    r = requests.post(ENDPOINT,
                      headers={"Authorization": f"Bearer {api_key}"},
                      files={"file": (name, pdf_bytes)})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    pages = r.json()["result"]["pages"]
    return pages[0]["markdown"] if pages else ""


def clean_markdown(md: str) -> str:
    md = LATEX_CMD.sub(r"\1", md)
    md = STRAY_LATEX.sub("", md)     # any $ or \ that survived unwrapped
    md = HTML_TAG.sub("\n", md)      # tag boundaries -> line breaks, not glued text
    md = NOTE.sub("", md)
    md = IMG_MD.sub("", md)
    md = HEADING.sub("", md)
    md = PAGE_NO.sub("", md)
    return re.sub(r"\n{2,}", "\n", md).strip()


def text_to_tokens(text: str, page_no: int, clean_word):
    """Words only — no geometry is available, so `box` is omitted entirely
    rather than fabricated."""
    tokens = []
    for raw in text.split():
        txt, fixes = clean_word(raw)
        if not txt.strip():
            continue
        tokens.append({
            "id": f"p{page_no:03d}w{len(tokens):03d}",
            "raw": raw,
            "txt": txt,
            "fixes": fixes,
        })
    return tokens
