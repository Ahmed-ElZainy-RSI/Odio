"""Check a book's OCR (work/qiyami/<book>/json) word by word against the PDF's own text layer. No API calls.

    python tools/qiyami/check_textlayer.py --pdf "books/الثقة_merged.pdf" --book الثقة
    python tools/qiyami/check_textlayer.py --pdf ... --book ... --pages 3 4 --all   # also list matching words

The «قيمي» PDFs carry the typesetter's text (letters AND tashkeel), damaged in two regular ways
that are undone here before comparing:
  1. lam-alef ligature extracted reversed: «الأيام» -> «األيام», «علامات» -> «عالمات», «لا» -> «ال».
     The OCR side is put into the same form (lam+alef -> alef+lam, marks after the lam).
  2. the mark on the last letter of a line run is cut off and lands at the start of a run
     (« ِّفِي ... وَلِكُل» is «... وَلِكُلِّ»). Runs around «» may also come out reversed, so the
     lost marks are kept per line and a word missing only one of them still counts as identical.
     For the same reason words are matched by their letters anywhere on the page, not by order.
Running headers/footers (lines with «|») are skipped; their lost mark goes to the title below. Diff kinds:
  marks   same letters, different tashkeel
  glued   the layer joined two words and lost their marks at the join: check by eye
  letters different letters
  missing / extra   a word only in the text layer / only in the OCR
"""
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

MARKS = "ًٌٍَُِّْٰ"
M = f"[{MARKS}]"
LETTER = r"[ء-يٱ]"
ALEFS = "اأإآ"
REPO = Path(__file__).resolve().parents[2]


def canon(w: str) -> str:
    """Tatweel out, «اً» == «ًا», one order for the marks on a letter."""
    w = w.replace("ـ", "")
    w = re.sub(r"ا(ً)", r"\1ا", w)
    return re.sub(f"{M}+", lambda m: "".join(sorted(m.group())), w)


def words(text: str) -> list[str]:
    """Arabic words with their marks; punctuation, digits and latin dropped."""
    out = []
    for t in text.split():
        t = re.sub(f"[^ء-يٱ{MARKS}ـ]", "", t)
        t = re.sub(f"^{M}+", "", t)
        if re.search(LETTER, t):
            out.append(canon(t))
    return out


def ref_words(page) -> list[tuple[str, list[str]]]:
    """(word, pool) per word. The pool holds the marks cut off line ends in that line: the text layer
    splits a line into runs around «» and may reverse them, so which word lost the mark is unknown."""
    out, carry = [], []
    for line in page.get_text().splitlines():
        if not line.strip():
            continue
        orphans = list(carry)

        def take(m):
            orphans.append(m.group(1)); return ""
        # a mark not preceded by a letter or another mark is a line-end mark that lost its letter
        fixed = re.sub(f"(?<![ء-يٱ{MARKS}ـ])({M}+)", take, line)
        if "|" in line:  # running header: its lost mark belongs to the title below it
            carry = orphans; continue
        carry = []
        # only a word at the edge of a run can have lost its mark: the line's last word, a word
        # touching «» or other punctuation, or a word whose run was reversed to the line start.
        # A mid-run word gets an empty pool, so a lost mark never hides a real mark error there.
        pool, toks = orphans, fixed.split()
        for k, t in enumerate(toks):
            for w in words(t):
                edge = (k == len(toks) - 1 or k == 0 or bool(re.search(r"[^ء-يٱً-ٰـ]", t))
                        or "|" in line)
                out.append((w, pool if edge else []))
    return out


def to_layer_form(w: str) -> str:
    """OCR word -> the text layer's reversed lam-alef form."""
    return canon(re.sub(f"ل({M}*)([{ALEFS}])({M}*)", r"\2ل\1\3", w))


def skel(w: str) -> str:
    return re.sub(M, "", w)


def key(w: str) -> str:
    """Letters for matching. The layer sometimes drops the alef of a lam-alef ligature («خلل» for
    خلال), so alefs next to a lam are ignored here, on both sides."""
    return re.sub(f"[{ALEFS}]*ل[{ALEFS}]*", "ل", skel(w))


def variants(ocr: str):
    """The OCR word (layer form) and the same word with one lam-alef alef dropped, as the layer may."""
    yield ocr
    for m in re.finditer(f"[{ALEFS}](?=ل)|(?<=ل{M})?[{ALEFS}]", ocr):
        if m.group():
            yield canon(ocr[:m.start()] + ocr[m.end():])


def same_word(ref: str, ocr: str, pool: list[str]) -> bool:
    """Equal, or equal once one of the line's lost marks is put back on the ref word's last letter."""
    for o in variants(ocr):
        if ref == o:
            return True
        for m in set(pool):
            if canon(ref + m) == o:
                pool.remove(m); return True
    return False


def diff_page(ref: list[tuple[str, list[str]]], ocr: list[str]):
    """Rows (kind, layer word, ocr word) in OCR order. Words the text layer has on another line
    (its runs come out of order) are matched by their letters anywhere on the page."""
    ocr_l = [to_layer_form(w) for w in ocr]
    rk, ok = [key(w) for w, _ in ref], [key(w) for w in ocr_l]
    pair = {}  # ocr index -> ref index
    for op, i1, i2, j1, j2 in SequenceMatcher(None, rk, ok, autojunk=False).get_opcodes():
        if op == "equal":
            pair.update(zip(range(j1, j2), range(i1, i2)))
    free_r = [i for i in range(len(ref)) if i not in pair.values()]
    for j in range(len(ocr)):
        if j not in pair:
            i = next((i for i in free_r if rk[i] == ok[j]), None)
            if i is not None:
                pair[j] = i; free_r.remove(i)
    # what is left: same position in what is left on both sides -> letters, else missing / extra
    left_o = [j for j in range(len(ocr)) if j not in pair]
    sm = SequenceMatcher(None, [rk[i] for i in free_r], [ok[j] for j in left_o], autojunk=False)
    lett, gone, more = {}, list(free_r), set(left_o)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            for a, b in zip(range(i1, i2), range(j1, j2)):
                lett[left_o[b]] = free_r[a]; gone.remove(free_r[a]); more.discard(left_o[b])
    rows, same = [], 0
    for j in range(len(ocr)):
        if j in pair:
            w, pool = ref[pair[j]]
            if same_word(w, ocr_l[j], pool):
                same += 1; rows.append(("ok", w, ocr[j]))
            else:
                rows.append(("marks", w, ocr[j]))
        elif j in lett:
            rows.append(("letters", ref[lett[j]][0], ocr[j]))
        else:
            rows.append(("extra", "", ocr[j]))
    # the layer glues a run's last word to the next one («أَخْطَاءَهُمالعِبْرَة»): two extra OCR words,
    # in either order (the OCR may read the heading before the line)
    for i in list(gone):
        w = ref[i][0]
        ex = [j for j, r in enumerate(rows) if r[0] == "extra"]
        hit = next(((x, y) for x in ex for y in ex
                    if x != y and key(w) == key(to_layer_form(rows[x][2])) + key(to_layer_form(rows[y][2]))), None)
        if hit:
            x, y = hit
            la, lb = to_layer_form(rows[x][2]), to_layer_form(rows[y][2])
            good = w in (canon(la + lb), canon(re.sub(f"{M}+$", "", la) + lb))
            rows[x] = ("ok" if good else "glued", w, rows[x][2] + " + " + rows[y][2])
            del rows[y]
            same += good; gone.remove(i)
    rows += [("missing", ref[i][0], "") for i in gone]
    return rows, same


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--out", default=str(REPO / "work" / "qiyami"), help="qiyami_run.py output root")
    ap.add_argument("--pages", nargs="+", type=int)
    ap.add_argument("--all", action="store_true", help="print matching words too")
    ap.add_argument("--json", help="write every diff row here")
    a = ap.parse_args()
    doc = pymupdf.open(a.pdf)
    root = Path(a.out) / a.book / "json"
    tot = {"ref": 0, "same": 0, "marks": 0, "glued": 0, "letters": 0, "missing": 0, "extra": 0}
    dump = []
    for n in a.pages or range(1, len(doc) + 1):
        f = root / f"p{n:03d}.json"
        ref = ref_words(doc[n - 1])
        if not ref or not f.exists():
            print(f"p{n:03d}: {'no text layer' if not ref else 'no OCR'}, skipped"); continue
        ocr = words(json.loads(f.read_text(encoding="utf-8"))["text"])
        rows, same = diff_page(ref, ocr)
        c = {k: sum(r[0] == k for r in rows) for k in ("marks", "glued", "letters", "missing", "extra")}
        tot["ref"] += len(ref); tot["same"] += same
        for k, v in c.items():
            tot[k] += v
        print(f"p{n:03d}: {same}/{len(ref)} identical  marks {c['marks']}  glued {c['glued']}  letters {c['letters']}"
              f"  missing {c['missing']}  extra {c['extra']}")
        for kind, r, o in rows:
            if kind != "ok" or a.all:
                print(f"    {kind:<8} layer «{r}»  ocr «{o}»")
            if kind != "ok":
                dump.append({"page": n, "kind": kind, "layer": r, "ocr": o})
    if tot["ref"]:
        print(f"\nTOTAL {tot['same']}/{tot['ref']} words identical ({tot['same'] / tot['ref']:.1%})  "
              f"marks {tot['marks']}  glued {tot['glued']}  letters {tot['letters']}  missing {tot['missing']}  extra {tot['extra']}")
    if a.json:
        Path(a.json).write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
