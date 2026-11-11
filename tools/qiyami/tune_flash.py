"""Experiments to raise Gemini-3.8-Flash tashkeel accuracy. Each variant writes
results_tune/<variant>/<page>/Gemini-3.8-Flash.txt, so it is scored with:

    python score_ocr.py --dir results_tune/<variant>

    python tune_flash.py --variant v2 --prompt v2
    python tune_flash.py --variant v2_high --prompt v2 --res high
    python tune_flash.py --variant v2_high_zoom --prompt v2 --res high --zoom 3
    python tune_flash.py --variant v2_high_zoom_vote --prompt v2 --res high --zoom 3 --samples 3

Pages default to the 7 scored pages. Needs $GEMINI_API_KEY. Every call uploads the page to Google.
"""
import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

import score_ocr as S

# same text as ocr_compare.TASHKEEL_PROMPT (not imported: ocr_compare needs gradio_client)
TASHKEEL_PROMPT = (
    "Extract all the text in this image exactly as it appears. Keep every Arabic diacritic "
    "(tashkeel: fatha, damma, kasra, sukun, shadda, tanween) exactly where it is printed: "
    "do not add diacritics that are not printed and do not drop any that are. "
    "Output only the extracted text, with no introduction, translation or description."
)


def redact(text: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    return text.replace(key, "<GEMINI_API_KEY>") if key and len(key) >= 8 else text


RUNS = Path("results_tune")  # qiyami_run.py points this at work/qiyami
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
PAGES = {"page_4": "page_4.png", "page_5": "page_5.png", "dino_5": "dino_5.jpg", "dino_8": "dino_8.jpg",
         "dino_11": "dino_11.jpg", "mummy_5": "mummy_5.jpg", "mummy_14": "mummy_14.jpg"}

PROMPT_V2 = """You are a diplomatic transcriber of a printed Arabic book page. Copy the text character by character, exactly as printed, including every diacritic mark (fatha, damma, kasra, sukun, shadda, tanween, superscript alef).

This book is only PARTIALLY vocalized, and inconsistently: some words are fully marked, some carry a single mark, most carry none. The same word can be marked on one line and bare on the next. Your output must reproduce this pattern mark by mark. It is NOT your job to vocalize correctly.

The two mistakes to avoid:
1. ADDING a mark that is not printed. Do not supply marks from your knowledge of grammar or spelling: no sukun the book did not print, no case ending the book did not print, no kasra/damma/shadda that "should" be there. If a letter has no visible mark above or below it, write it bare.
2. DROPPING a mark that is printed. Short common words often carry a lone mark that grammar does not require (e.g. a fatha on the dhal of a demonstrative, a kasra on the fa of a preposition, a fatha on the lam of a preposition, a kasra on the final nun before a hamzat wasl). Copy those marks; do not normalize the word to its usual bare spelling.

Also:
- If a mark is printed but you think it is the wrong mark, copy it anyway. Copy tanween as tanween and a single short vowel as a single short vowel; check carefully, they look alike when small.
- Do not correct spelling mistakes or typos.
- Keep the printed line breaks. Include headings and captions. Skip page numbers and watermarks.
- Before writing each word, look at the zone above and below every letter of that word.

Output only the transcribed text: no introduction, notes, translation or markdown."""

ZOOM_NOTE = (
    "\n\nThe first image is the whole page; use it for reading order. The following images are "
    "enlarged, overlapping horizontal strips of the same page, top to bottom. Read the diacritics "
    "from the enlarged strips. Transcribe the page once (do not repeat text that appears in two "
    "overlapping strips)."
)

REVIEW_PROMPT = """Below is a draft transcription of this printed Arabic page. The letters are correct; only the diacritics may be wrong. Check the draft word by word against the enlarged strips.

The book is only partially vocalized. For every word, look above and below each letter in the image and make the draft's marks match what is printed:
- remove any mark from the draft that is not visible in the image (do not keep a mark because grammar needs it);
- add any printed mark the draft is missing, including a lone mark on a short word (a fatha on the dhal of هذا, a kasra on the fa of في, a fatha on the lam of على, a kasra on a final nun);
- fix a mark of the wrong kind (tanween vs single vowel, fatha vs damma).
Do not change letters, words, spacing or line breaks.

Output only the corrected text.

DRAFT:
{draft}"""

PROMPTS = {"v1": TASHKEEL_PROMPT, "v2": PROMPT_V2}
RES = {"default": None, "low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM",
       "high": "MEDIA_RESOLUTION_HIGH"}


def jpeg_part(img: Image.Image, quality=92) -> dict:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def enhance(img: Image.Image) -> Image.Image:
    """Grayscale, stretch contrast, sharpen: thin marks on coloured backgrounds stand out."""
    from PIL import ImageFilter, ImageOps
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    return g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


def strips(img: Image.Image, n: int, scale: float = 2.0, overlap: float = 0.08):
    """n overlapping horizontal strips, each enlarged `scale` times."""
    w, h = img.size
    step = h / n
    out = []
    for k in range(n):
        y0 = max(0, int(k * step - overlap * h))
        y1 = min(h, int((k + 1) * step + overlap * h))
        s = img.crop((0, y0, w, y1))
        out.append(s.resize((int(w * scale), int((y1 - y0) * scale)), Image.Resampling.LANCZOS))
    return out


def call(model, parts, res, thinking, temperature, key, retries=4):
    cfg = {"temperature": temperature}
    if res:
        cfg["mediaResolution"] = res
    if thinking:
        cfg["thinkingConfig"] = {"thinkingLevel": thinking}
    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": cfg}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(URL.format(model), body, {"x-goog-api-key": key,
                                                               "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
            cand = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in cand if not p.get("thought")), data.get("usageMetadata", {})
        except urllib.error.HTTPError as e:
            msg = e.read(1000).decode(errors="replace")
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(4 * 2 ** attempt); continue
            raise RuntimeError(redact(f"HTTP {e.code}: {msg[:400]}")) from None


def vote(texts: list[str]) -> str:
    """Per-letter majority vote on diacritics. Letters come from the sample whose letters agree most
    with the others; each of its letters takes the mark set most samples put on the aligned letter."""
    normed = [S.norm(t, True) for t in texts]
    units = [S.units(t) for t in normed]
    letters = ["".join(c for c, _ in u) for u in units]
    base = max(range(len(texts)), key=lambda i: -sum(S.lev(letters[i], letters[j]) for j in range(len(texts))))
    bu = units[base]
    ballots = [[m] for _, m in bu]
    for j, u in enumerate(units):
        if j == base:
            continue
        for bi, ui in S.aligned_pairs([c for c, _ in bu], [c for c, _ in u]):
            ballots[bi].append(u[ui][1])
    marks = []
    for (c, m), b in zip(bu, ballots):
        if c == " ":
            continue
        best, cnt = Counter(b).most_common(1)[0]
        # a tie keeps the base sample's own mark
        marks.append(best if cnt > len(b) / 2 or best == m else m)
    return remark(texts[base], marks)


def remark(raw: str, marks: list[str]) -> str:
    """Put voted marks back on the base sample's own text, keeping its punctuation and line breaks.
    The k-th Arabic letter of `raw` is the k-th letter of norm(raw) (norm drops no letters here)."""
    import re
    letter = re.compile(r"[ء-غف-يٱ]")
    out, k = [], 0
    for ch in raw:
        if re.match(S.MARK_RE, ch) or ch == "ـ":
            continue
        out.append(ch)
        if letter.match(ch):
            if k < len(marks):
                out.append(marks[k])
            k += 1
    if k != len(marks):  # letters did not line up: return the normalized vote instead
        it = iter(marks)
        return "".join(c if c == " " else c + next(it) for c in S.norm(raw))
    return "".join(out)


def run_page(page, args, key):
    img = Image.open(Path("pages") / PAGES[page])
    prompt = PROMPTS[args.prompt]
    parts = [jpeg_part(img)] if img.format == "JPEG" else [jpeg_part(img, 95)]
    if args.zoom:
        parts += [jpeg_part(enhance(s) if args.enhance else s) for s in strips(img, args.zoom, args.scale)]
        prompt += ZOOM_NOTE
    parts.append({"text": prompt})
    texts, toks = [], 0
    image_parts = parts[:-1]
    with ThreadPoolExecutor(args.samples) as ex:
        futs = [ex.submit(call, args.model, parts, RES[args.res], args.thinking,
                          args.temperature if args.samples == 1 else max(args.temperature, 0.4), key)
                for _ in range(args.samples)]
        for f in futs:
            t, u = f.result()
            texts.append(t); toks += u.get("totalTokenCount", 0)
    out = RUNS / args.variant / page
    out.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(texts):
        (out / f"sample_{i}.raw").write_text(t, encoding="utf-8")
    final = texts[0] if args.samples == 1 else vote(texts)
    if args.review:
        final, u = call(args.model, image_parts + [{"text": REVIEW_PROMPT.format(draft=final)}],
                        RES[args.res], args.thinking, 0.0, key)
        toks += u.get("totalTokenCount", 0)
        (out / "draft.raw").write_text(texts[0] if args.samples == 1 else vote(texts), encoding="utf-8")
    (out / "Gemini-3.8-Flash.txt").write_text(final, encoding="utf-8")
    return page, S.score(final, S.GT[page]), toks


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--prompt", default="v2", choices=PROMPTS)
    ap.add_argument("--res", default="default", choices=RES)
    ap.add_argument("--thinking", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--zoom", type=int, default=0, help="also send N enlarged horizontal strips")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--samples", type=int, default=1, help=">1: per-letter majority vote on diacritics")
    ap.add_argument("--enhance", action="store_true", help="contrast-enhance the zoomed strips")
    ap.add_argument("--review", action="store_true", help="second pass: check the draft's diacritics against the image")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--pages", nargs="+", default=list(PAGES), choices=PAGES)
    args = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY") or sys.exit("needs $GEMINI_API_KEY")
    rows, total_toks = [], 0
    with ThreadPoolExecutor(4) as ex:
        for page, sc, toks in ex.map(lambda p: run_page(p, args, key), args.pages):
            rows.append((page, sc)); total_toks += toks
            c, w, t, ad, fa = sc
            print(f"{page:<10} words {w:6.1%}  tashkeel {t:6.1%}  added {ad:5.1%}  overall {(w + t) / 2:6.1%}")
    n = len(rows)
    w = sum(r[1][1] for r in rows) / n
    t = sum(r[1][2] for r in rows) / n
    print(f"{args.variant:<10} words {w:6.1%}  tashkeel {t:6.1%}  overall {(w + t) / 2:6.1%}   tokens {total_toks}")


if __name__ == "__main__":
    main()
