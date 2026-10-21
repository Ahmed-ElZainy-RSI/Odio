"""Layer 01, OCR path — for PDFs with no embedded text layer.

Eleven of the 36 books in assets/ are genuinely scanned: المومياوات has 0
extractable characters across 33 pages and 158 images. Those cannot use the
fast path in extract.py and need real recognition.

Engine: Gemini (vision) with a response schema. The schema is not cosmetic —
asked in prose for `box_2d`, the model returned text perfectly and `None` for
every box. Forcing the field through response_schema produced valid boxes on
every line, verified by overlaying them on the rendered page.

IMPORTANT — granularity differs from the text-layer path:
    text layer  -> one box per WORD  (exact, from the PDF)
    OCR         -> one box per LINE  (from the model)
Word boxes are subdivided from the line box proportionally by letter count,
right-to-left. They are approximations and are labelled `box_level: "line"`
so the reader can say so rather than implying per-word precision.
"""
import io
import json
import time

import pymupdf
from PIL import Image
from google import genai
from google.genai import types

from pipeline import usage

MODEL = "gemini-3.6-flash"
RENDER_SCALE = 2.0
MAX_EDGE = 1100

SCHEMA = types.Schema(
    type="ARRAY",
    items=types.Schema(
        type="OBJECT",
        properties={
            "text": types.Schema(type="STRING"),
            "box_2d": types.Schema(type="ARRAY",
                                   items=types.Schema(type="INTEGER"),
                                   min_items=4, max_items=4),
        },
        required=["text", "box_2d"],
    ),
)

PROMPT = (
    "Detect the 2D bounding box of every Arabic text line in this image.\n"
    "For each line output its text verbatim — keep diacritics exactly as printed, "
    "do not add or remove any — and box_2d as [ymin, xmin, ymax, xmax] "
    "normalised to 0-1000.\n"
    "Right-to-left reading order, top to bottom. Skip decorative artwork, "
    "page numbers and watermarks."
)


def render_page(page, scale=RENDER_SCALE, max_edge=MAX_EDGE):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
    im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    im.thumbnail((max_edge, max_edge * 3), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue(), im.size


def ocr_page(client, jpeg: bytes, retries=4):
    """Returns [{text, box_2d}] or raises after exhausting retries."""
    last = None
    for attempt in range(retries):
        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=SCHEMA,
                ),
            )
            # recorded before parsing: a response that fails to parse was still billed
            usage.record("gemini", MODEL, "ocr", images=1, **usage.gemini_units(r))
            return json.loads(r.text)
        except Exception as e:
            last = e
            # 429/503 are transient here; back off rather than abandoning the page
            time.sleep(2 ** attempt * 2)
    raise last


def lines_to_tokens(lines, page_no, clean_word):
    """Split model lines into word tokens, subdividing each line box RTL.

    The line box is the only real measurement; word boxes inside it are
    proportional to letter count and therefore approximate.
    """
    tokens = []
    for line in lines:
        words = [w for w in (line.get("text") or "").split() if w.strip()]
        if not words:
            continue
        ymin, xmin, ymax, xmax = line["box_2d"]
        # normalise 0-1000 -> percentage of page
        x0, y0 = xmin / 10.0, ymin / 10.0
        w_pct, h_pct = (xmax - xmin) / 10.0, (ymax - ymin) / 10.0

        # A "word" that is pure punctuation cleans down to nothing (the credits
        # page produced 14 of these). Dropping them here keeps invisible,
        # clickable, untimed spans out of the reader.
        pairs = [(clean_word(w), w) for w in words]
        pairs = [(c, raw) for c, raw in pairs if c[0].strip()]
        if not pairs:
            continue
        cleaned = [c for c, _ in pairs]
        words = [raw for _, raw in pairs]
        weights = [max(1, len(c[0])) for c in cleaned]
        total = sum(weights)
        acc = 0.0
        for (txt, fixes), raw, wt in zip(cleaned, words, weights):
            share = w_pct * wt / total
            # right-to-left: first word occupies the RIGHTMOST slice
            bx = x0 + w_pct - acc - share
            acc += share
            tokens.append({
                "id": f"p{page_no:03d}w{len(tokens):03d}",
                "raw": raw,
                "txt": txt,
                "fixes": fixes,
                "box": [round(bx, 3), round(y0, 3), round(share, 3), round(h_pct, 3)],
            })
    return tokens
