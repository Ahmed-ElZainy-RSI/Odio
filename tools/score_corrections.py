"""How accurate is a book's text, and did correction help or hurt? (spec 019)

Scores a book's page records against hand-made reference transcripts:

    references/<book>/pNNN.txt     one page per file, UTF-8, the words as printed

For every page that has a reference it reports, before correction and after:
  - WER and CER on letters (true edit distance; diacritics, tatweel and punctuation are
    ignored, but alef forms and ة/ه are NOT folded -- those are real OCR errors here);
  - per word, via the stable token ids: **caught** (wrong before, right after), **false
    edit** (right before, wrong after), **missed** (wrong both times);
  - DER: diacritic errors per letter on words whose letters match, where the reference
    itself is diacritised.

"Before" is each token's `ocr_txt` where a correction was applied, else its `txt`; "after"
is `txt`. A token a correction deleted outright is gone from the record, so it can only
show up as a deletion in "after" -- a limitation of what the records keep.

Usage:
    python3 tools/score_corrections.py "work/<book>-lighton" references/<book>
    python3 tools/score_corrections.py "work/<book>-lighton" references/<book> --json
"""
import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

MARKS = re.compile("[\u0610-\u061a\u064b-\u0652\u0670\u06d6-\u06ed\u0640]")
LETTER = re.compile("[\u0621-\u064a\u0671-\u06d3]")
REF_FILE = re.compile(r"^p(\d{3,})\.txt$")


def letters(word: str) -> str:
    """The word's letters and digits only: marks, tatweel and punctuation dropped."""
    w = MARKS.sub("", unicodedata.normalize("NFC", word))
    return "".join(c for c in w if unicodedata.category(c)[0] in "LN")


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def _marks_by_letter(word: str) -> list[str]:
    """The diacritics attached to each Arabic letter of a word, in order."""
    out: list[str] = []
    for c in unicodedata.normalize("NFC", word):
        if LETTER.match(c):
            out.append("")
        elif MARKS.match(c) and c != "\u0640" and out:
            out[-1] += c
    return ["".join(sorted(m)) for m in out]


def _correct_tokens(ref: list[str], hyp: list[tuple[str, str]]) -> set[str]:
    """Ids of hypothesis tokens aligned to an identical reference word."""
    matcher = SequenceMatcher(a=[letters(w) for w in ref], b=[letters(t) for _, t in hyp],
                              autojunk=False)
    ok = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ok.update(hyp[j][0] for j in range(j1, j2))
    return ok


@dataclass
class PageScore:
    page: int
    ref_words: int
    wer_before: float
    wer_after: float
    cer_before: float
    cer_after: float
    caught: list[str] = field(default_factory=list)
    false_edits: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    der_after: float | None = None
    der_letters: int = 0


def score_page(page: int, ref_text: str, record: dict) -> PageScore:
    ref = ref_text.split()
    after = [(t["id"], t["txt"]) for t in record["tokens"]]
    before = [(t["id"], t.get("ocr_txt", t["txt"])) for t in record["tokens"]]
    ref_l = [letters(w) for w in ref]

    def wer(hyp):
        return edit_distance(ref_l, [letters(t) for _, t in hyp]) / max(1, len(ref))

    def cer(hyp):
        a, b = "".join(ref_l), "".join(letters(t) for _, t in hyp)
        return edit_distance(list(a), list(b)) / max(1, len(a))

    ok_before, ok_after = _correct_tokens(ref, before), _correct_tokens(ref, after)
    ids = [i for i, _ in after]
    score = PageScore(
        page=page, ref_words=len(ref),
        wer_before=round(wer(before), 4), wer_after=round(wer(after), 4),
        cer_before=round(cer(before), 4), cer_after=round(cer(after), 4),
        caught=[i for i in ids if i in ok_after and i not in ok_before],
        false_edits=[i for i in ids if i in ok_before and i not in ok_after],
        missed=[i for i in ids if i not in ok_before and i not in ok_after],
    )

    # DER, only where the reference is printed with tashkeel and the letters match
    ref_marks = sum(1 for w in ref for m in _marks_by_letter(w) if m)
    ref_letters = sum(len(_marks_by_letter(w)) for w in ref)
    if ref_letters and ref_marks / ref_letters >= 0.15:
        matcher = SequenceMatcher(a=ref_l, b=[letters(t) for _, t in after], autojunk=False)
        wrong = total = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                continue
            for r, (_, h) in zip(ref[i1:i2], after[j1:j2], strict=True):
                rm, hm = _marks_by_letter(r), _marks_by_letter(h)
                if len(rm) != len(hm):
                    continue
                total += len(rm)
                wrong += sum(x != y for x, y in zip(rm, hm, strict=True))
        if total:
            score.der_after, score.der_letters = round(wrong / total, 4), total
    return score


def run(work_dir: Path, ref_dir: Path, as_json: bool = False) -> dict:
    pages = []
    for ref_path in sorted(Path(ref_dir).iterdir()):
        m = REF_FILE.match(ref_path.name)
        if not m:
            continue
        number = int(m.group(1))
        rec_path = Path(work_dir) / f"p{number:03d}.json"
        if not rec_path.exists():
            print(f"p{number:03d}: reference but no page record, skipped", file=sys.stderr)
            continue
        record = json.loads(rec_path.read_text(encoding="utf-8"))
        pages.append(score_page(number, ref_path.read_text(encoding="utf-8"), record))

    words = sum(p.ref_words for p in pages)

    def weighted(attr):
        return round(sum(getattr(p, attr) * p.ref_words for p in pages) / words, 4) if words else None

    der_letters = sum(p.der_letters for p in pages)
    report = {
        "book": Path(work_dir).name, "pages": len(pages), "ref_words": words,
        "wer_before": weighted("wer_before"), "wer_after": weighted("wer_after"),
        "cer_before": weighted("cer_before"), "cer_after": weighted("cer_after"),
        "caught": sum(len(p.caught) for p in pages),
        "false_edits": sum(len(p.false_edits) for p in pages),
        "missed": sum(len(p.missed) for p in pages),
        "der_after": round(sum((p.der_after or 0) * p.der_letters for p in pages) / der_letters, 4)
                     if der_letters else None,
        "per_page": [asdict(p) for p in pages],
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print(report)
    return report


def _print(r: dict) -> None:
    print(f"{r['book']}: {r['pages']} page(s) with a reference, {r['ref_words']} reference words\n")
    if not r["pages"]:
        print("nothing to score -- add references/<book>/pNNN.txt (see references/README.md)")
        return
    print(f"{'':12}{'before':>10}{'after':>10}")
    for k in ("wer", "cer"):
        print(f"{k.upper():12}{r[k + '_before']:>10.2%}{r[k + '_after']:>10.2%}")
    if r["der_after"] is not None:
        print(f"{'DER':12}{'':>10}{r['der_after']:>10.2%}")
    print(f"\ncaught {r['caught']} · false edits {r['false_edits']} · missed {r['missed']}")
    for p in r["per_page"]:
        if p["false_edits"]:
            print(f"  p{p['page']:03d} false edits: {', '.join(p['false_edits'])}")


def main():
    ap = argparse.ArgumentParser(description="Score a book's text against reference transcripts.")
    ap.add_argument("work_dir")
    ap.add_argument("ref_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    run(Path(args.work_dir), Path(args.ref_dir), as_json=args.json)


if __name__ == "__main__":
    main()
