"""Turn check_textlayer.py's diff rows into tashkeel fixes for tools/qiyami/fixes/<book>.json. No API calls.

    python tools/qiyami/tashkeel_review.py --pdf "books/دار الريادة/الطاعة.pdf" --book الطاعة --sheets work/review

Reads work/qiyami/<book>/textlayer_diff.json (check_textlayer.py --json). For every «marks» row (same letters,
different tashkeel) the text layer's spelling is the candidate. It is accepted without a human look only when
two independent readings agree on it: the text layer AND at least one of the 5 Gemini samples of that page.
Everything else goes to review: <sheets>/<book>_NN.png (the word cut from the page at 600 dpi, numbered)
with <sheets>/<book>_review.tsv (number, page, OCR word, layer word). Rows judged by eye are added to
work/qiyami/<book>/review_decisions.json as {"n": 3, "new": "..."} (or "keep") and merged by --merge.

    python tools/qiyami/tashkeel_review.py --pdf ... --book ... --merge    # write fixes/<book>.json
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MARKS = "ًٌٍَُِّْٰ"
M = f"[{MARKS}]"


def canon(w):
    w = w.replace("ـ", "")
    w = re.sub(r"ا(ً)", r"\1ا", w)
    w = re.sub(f"^{M}+", "", w)
    return re.sub(f"{M}+", lambda m: "".join(sorted(m.group())), w)


def skel(w):
    return re.sub(M, "", w)


def words(text):
    out = []
    for t in text.split():
        t = re.sub(f"[^ء-يٱ{MARKS}ـ]", "", t)
        if re.search(r"[ء-ي]", t):
            out.append(canon(t))
    return out


def lamalef(ocr_word):
    """A lam-alef ligature (لا لأ لإ لآ): the layer stores it reversed, so its marks cannot be copied back."""
    return bool(re.search(f"ل{M}*[اأإآ]", ocr_word))


def per_letter(w):
    return [set(m) for m in re.findall(f"[ء-يٱ]({M}*)", w)]


def only_drops(layer, ocr):
    """Every letter's marks in the layer are a subset of the OCR's (the layer adds nothing)."""
    a, b = per_letter(layer), per_letter(ocr)
    return len(a) == len(b) and all(x <= y for x, y in zip(a, b))


def split_last(w):
    """(everything up to the last letter's marks, the last letter's marks)."""
    m = re.search(f"{M}*$", w)
    return w[:m.start()], m.group()


def locate(page, layer_word):
    """Rects of layer words with the same letters (lam-alef order ignored)."""
    k = re.sub(f"[اأإآ]*ل[اأإآ]*", "ل", skel(canon(layer_word)))
    out = []
    for x0, y0, x1, y1, t, *_ in page.get_text("words"):
        t = re.sub(f"[^ء-يٱ{MARKS}ـ]", "", t)
        if re.sub(f"[اأإآ]*ل[اأإآ]*", "ل", skel(canon(t))) == k:
            out.append(pymupdf.Rect(x0, y0, x1, y1))
    return out


LETTER = re.compile(r"[ء-يٱ]")


def with_quotes(text, old, new):
    """The diff strips punctuation, so «وَ«رَامِي»» reaches us as «وَرَامِي», which is not in the text. Find the
    word with quote marks inside it and put the same marks into `new` after the same number of letters."""
    edge = f"[ء-يٱ{MARKS}ـ]"
    if re.search(f"(?<!{edge}){re.escape(old)}(?!{edge})", text):
        return old, new
    pat = "[«».،:؛!؟]*".join(re.escape(c) for c in old)
    m = re.search(f"(?<!{edge}){pat}(?!{edge})", text)
    if not m:
        return old, new
    inserts, letters = [], 0  # (letters before the quote, quote)
    for c in m.group():
        if c in "«».،:؛!؟":
            inserts.append((letters, c))
        elif LETTER.match(c):
            letters += 1
    out, letters, k = "", 0, 0
    for i, c in enumerate(new):
        if LETTER.match(c):
            while k < len(inserts) and inserts[k][0] == letters:  # quote goes before this letter
                out += inserts[k][1]; k += 1
            letters += 1
        out += c
    out += "".join(q for _, q in inserts[k:])
    return m.group(), out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--sheets", default=str(REPO / "work" / "review"))
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    book_dir = REPO / "work" / "qiyami" / a.book
    rows = json.loads((book_dir / "textlayer_diff.json").read_text(encoding="utf-8"))
    fix_file = HERE / "fixes" / f"{a.book}.json"
    existing = json.loads(fix_file.read_text(encoding="utf-8")) if fix_file.exists() else []
    have = {(f["page"], f["old"]) for f in existing}
    pdf = pymupdf.open(a.pdf)

    # OCR words per page (after existing fixes) and the 5 samples' words
    ocr_words, sample_words = {}, {}
    for p in sorted((book_dir / "json").glob("p*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        ocr_words[d["page"]] = Counter(words(d["text"]))
        raws = list((book_dir / f"page_{d['page']}").glob("sample_*.raw"))
        sample_words[d["page"]] = set(w for r in raws for w in words(r.read_text(encoding="utf-8")))

    auto, review = [], []
    targets = defaultdict(set)  # (page, ocr word) -> layer forms wanted
    for r in rows:
        if r["kind"] == "marks":
            targets[(r["page"], canon(r["ocr"]))].add(canon(r["layer"]))
    for r in rows:
        if r["kind"] != "marks":
            if r["kind"] in ("letters", "glued"):
                review.append({**r, "why": r["kind"]})
            continue
        n, o, L = r["page"], canon(r["ocr"]), canon(r["layer"])
        if (n, o) in have:
            continue
        if lamalef(o) or skel(L) != skel(o):
            review.append({**r, "why": "lam-alef / letters"}); continue
        # the same OCR word twice on the page with different targets: a whole-word fix would hit both
        if ocr_words[n][o] > 1 and (len(targets[(n, o)]) > 1 or ocr_words[n][o] != sum(
                1 for x in rows if x["kind"] == "marks" and x["page"] == n and canon(x["ocr"]) == o)):
            review.append({**r, "why": "word repeats on the page"}); continue
        (Lb, Lt), (ob, ot) = split_last(L), split_last(o)
        if L in sample_words.get(n, ()):
            auto.append({"page": n, "old": o, "new": L, "why": "text layer, also read so by a Gemini sample"})
        elif only_drops(L, o):
            # the layer only lacks marks the OCR has: it can lose marks (seen: «كُلّه» for printed «كُلُّه»)
            review.append({**r, "why": "layer drops a mark"})
        elif Lt or not ot:
            # the layer has a mark on the last letter (or neither has one): nothing was lost at a run edge,
            # so the typesetter's string is what is printed
            auto.append({"page": n, "old": o, "new": L, "why": "text layer (typeset string)"})
        elif Lb != ob:
            # inside differs, last mark only in the OCR (maybe cut off the layer): take the layer's inside,
            # and check the ending by eye
            review.append({**r, "why": "ending: layer has none", "inside": Lb + ot})
        else:
            review.append({**r, "why": "ending: layer has none"})
    # dedupe auto (same page/old)
    seen, auto_u = set(), []
    for f in auto:
        if (f["page"], f["old"]) not in seen:
            seen.add((f["page"], f["old"])); auto_u.append(f)

    out = Path(a.sheets); out.mkdir(parents=True, exist_ok=True)
    dec_file = book_dir / "review_decisions.json"
    if a.merge:
        decisions = json.loads(dec_file.read_text(encoding="utf-8")) if dec_file.exists() else []
        # decisions: {"page": 5, "ocr": "<OCR word>", "new": "<as printed>" | "keep", "why": "..."}
        added, judged = [], set()
        for d in decisions:
            judged.add((d["page"], canon(d["ocr"])))
            if d["new"] == "keep" or canon(d["new"]) == canon(d["ocr"]):
                continue
            added.append({"page": d["page"], "old": canon(d["ocr"]), "new": d["new"],
                          "why": d.get("why", "checked on the page image")})
        # a word judged by eye overrides the automatic rule
        auto_u = [f for f in auto_u if (f["page"], f["old"]) not in judged]
        merged, keys = list(existing), set(have)
        for f in added + auto_u:
            if (f["page"], f["old"]) not in keys:
                keys.add((f["page"], f["old"]))
                raw = book_dir / f"page_{f['page']}" / "Gemini-3.8-Flash.txt"
                if raw.exists():
                    old, new = with_quotes(raw.read_text(encoding="utf-8"), f["old"], f["new"])
                    f = {**f, "old": old, "new": new}
                merged.append(f)
        # on each page, fixes of several words (one occurrence picked by its neighbour) go before one-word fixes
        merged.sort(key=lambda f: (f["page"], -len(f["old"].split())))
        # apply in order, as qiyami_run.apply_fixes does: a fix whose word an earlier fix already turned into
        # the same `new` (e.g. «سَمِيرٌ» then «وَ«سَمِيرٌ») is dropped instead of failing the run
        edge = f"[ء-يٱ{MARKS}ـ]"
        texts, kept = {}, []
        for f in merged:
            raw = book_dir / f"page_{f['page']}" / "Gemini-3.8-Flash.txt"
            if f["page"] not in texts:
                texts[f["page"]] = raw.read_text(encoding="utf-8") if raw.exists() else None
            t = texts[f["page"]]
            if t is None:
                kept.append(f); continue
            pat = re.compile(f"(?<!{edge}){re.escape(f['old'])}(?!{edge})")
            if not pat.search(t) and f["new"] and f["new"] in t:
                print(f"  p{f['page']}: {f['old']!r} already done by an earlier fix, dropped")
                continue
            texts[f["page"]] = pat.sub(lambda m: f["new"], t)
            kept.append(f)
        merged = kept
        fix_file.write_text("[\n" + ",\n".join(" " + json.dumps(f, ensure_ascii=False) for f in merged) + "\n]\n",
                            encoding="utf-8")
        print(f"{a.book}: {len(existing)} existing + {len(auto_u)} auto + {len(added)} by eye -> {fix_file}")
        return

    # a random sample of the automatic fixes goes on the sheets too ("why": "spot check"), so the rule
    # "the text layer is what is printed" is checked by eye on every book
    import random
    rng = random.Random(a.book)
    spot = rng.sample(auto_u, min(len(auto_u), max(8, len(auto_u) // 8)))
    review += [{"page": f["page"], "kind": "marks", "layer": f["new"], "ocr": f["old"], "why": "spot check"}
               for f in spot]
    # words already judged by eye are not shown again
    done = set()
    if dec_file.exists():
        done = {(d["page"], canon(d["ocr"])) for d in json.loads(dec_file.read_text(encoding="utf-8"))}
    review = [r for r in review if (r["page"], canon(r["ocr"].split(" + ")[0])) not in done]
    rev = []
    tiles = []
    for k, r in enumerate(review, 1):
        rev.append({"n": k, **r})
        page = pdf[r["page"] - 1]
        rs = locate(page, r["layer"] or r["ocr"])[:2]
        ims = []
        for rc in rs:
            pm = page.get_pixmap(dpi=600, clip=pymupdf.Rect(rc.x0 - 4, rc.y0 - 7, rc.x1 + 4, rc.y1 + 7) & page.rect)
            ims.append(Image.frombytes("RGB", (pm.width, pm.height), pm.samples))
        W = 110 + sum(i.width + 12 for i in ims)
        H = max([i.height for i in ims] + [70])
        t = Image.new("RGB", (W, H), "white")
        x = 110
        for i in ims:
            t.paste(i, (x, 0)); x += i.width + 12
        ImageDraw.Draw(t).text((6, 6), f"#{k}", fill="red")
        tiles.append(t)
    per = 10
    for s in range(0, len(tiles), per):
        ch = tiles[s:s + per]
        W, H = max(t.width for t in ch), sum(t.height + 6 for t in ch)
        sheet = Image.new("RGB", (W, H), (215, 215, 215))
        y = 0
        for t in ch:
            sheet.paste(t, (0, y)); y += t.height + 6
        sheet.save(out / f"{a.book}_{s // per + 1:02d}.png")
    (out / f"{a.book}_review.json").write_text(json.dumps(rev, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / f"{a.book}_review.tsv").write_text(
        "\n".join(f"{x['n']}\tp{x['page']}\t{x['why']}\tOCR {x['ocr']}\tLAYER {x['layer']}" for x in rev),
        encoding="utf-8")
    print(f"{a.book}: {sum(1 for r in rows if r['kind'] == 'marks')} mark diffs | auto {len(auto_u)} | "
          f"review {len(rev)} ({(len(tiles) + per - 1) // per} sheets)")


if __name__ == "__main__":
    main()
