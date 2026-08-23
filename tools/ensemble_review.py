"""Offline audit: diff LightOn's OCR against a second engine's OCR, page by page.

Standalone and read-only -- writes a separate report, never touches the
run_ocr_*.py output it reads. Implements the two-engine-vote idea from
docs/ocr-correction-approaches-en.md (Approach 3) at whole-page granularity
via text alignment, instead of per-word crops -- LightOn's /parse and
/search never return bounding boxes to crop with (see
tmp/lighton_bbox_support_request.md), so there is nothing to crop against.

No Context Model, no per-engine confidence score (neither run_ocr_lighton.py
nor run_ocr_gemini.py emits one). The policy is therefore binary: aligned
tokens that still match after orthographic normalization -> KEEP, anything
else -> FLAG for human review. A suspicion-score tiebreak needs a signal
this POC deliberately doesn't build yet.

Usage:
    python3 tools/ensemble_review.py "work/<book>" "work/<book>-lighton"
    python3 tools/ensemble_review.py "work/<book>" "work/<book>-lighton" --out custom.json
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pipeline.extract import COMBINING  # noqa: E402

ALIF_FORMS = re.compile(r"[أإآٱ]")
TATWEEL = "ـ"


def normalize_for_compare(word: str) -> str:
    """Fold orthographic variants together for comparison only.

    Both engines' text is compared after this; the stored/reported text is
    always the original (still diacritized, un-folded) txt -- this function
    never touches what gets written, only the copy used to decide
    equal/not-equal, per the doc's "normalize before tallying, return the
    original form" rule.
    """
    w = word.replace(TATWEEL, "")
    w = COMBINING.sub("", w)  # strip diacritics/shadda for comparison only
    w = ALIF_FORMS.sub("ا", w)
    w = w.replace("ة", "ه").replace("ى", "ي")
    # punctuation the two engines attach inconsistently (مِصْرَ، vs مِصْرَ,
    # markdown ** emphasis, trailing periods) -- strip everything but
    # letters/digits so it never drives a false disagreement
    w = re.sub(r"[^\w]", "", w, flags=re.UNICODE)
    return w


def load_tokens(page_path: Path) -> list[dict]:
    return json.loads(page_path.read_text(encoding="utf-8"))["tokens"]


def find_pages(d: Path) -> dict[int, Path]:
    return {int(p.stem[1:]): p for p in d.glob("p*.json")}


def align_page(gemini_tokens: list[dict], lighton_tokens: list[dict]) -> list[dict]:
    """Returns [{id, gemini_id, lighton_txt, gemini_txt, agree, action}, ...].

    gemini_id is the specific Gemini token a LightOn token was paired with
    (present for 'equal'/'replace' pairs, absent for delete/insert entries
    with no counterpart on the other side) -- build_reader.py reuses it to
    borrow that token's box when LightOn has none of its own.

    Alignment runs on normalize_for_compare(txt) so the two engines' text is
    compared after orthographic folding; the words stored in the result are
    each engine's original txt. difflib.SequenceMatcher rather than a
    hand-rolled aligner: cheap, stdlib, and this compares two reads of the
    *same* page (mostly aligned, with localized edits) rather than unrelated
    documents, which is exactly its strong case.
    """
    g_words = [t["txt"] for t in gemini_tokens]
    l_words = [t["txt"] for t in lighton_tokens]
    g_norm = [normalize_for_compare(w) for w in g_words]
    l_norm = [normalize_for_compare(w) for w in l_words]

    results = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=g_norm, b=l_norm, autojunk=False).get_opcodes():
        if tag == "equal":
            for gi, li in zip(range(i1, i2), range(j1, j2)):
                results.append({
                    "id": lighton_tokens[li]["id"],
                    "gemini_id": gemini_tokens[gi]["id"],
                    "lighton_txt": l_words[li],
                    "gemini_txt": g_words[gi],
                    "agree": True,
                    "action": "KEEP",
                })
            continue

        # replace/delete/insert: pair positionally as far as both sides go,
        # then whatever's left on the longer side is a token one engine has
        # and the other doesn't -- reported, not silently dropped.
        paired = min(i2 - i1, j2 - j1)
        for gi, li in zip(range(i1, i1 + paired), range(j1, j1 + paired)):
            results.append({
                "id": lighton_tokens[li]["id"],
                "gemini_id": gemini_tokens[gi]["id"],
                "lighton_txt": l_words[li],
                "gemini_txt": g_words[gi],
                "agree": False,
                "action": "FLAG",
            })
        for gi in range(i1 + paired, i2):
            results.append({
                "id": f"gemini-only:{gemini_tokens[gi]['id']}",
                "lighton_txt": None,
                "gemini_txt": g_words[gi],
                "agree": False,
                "action": "FLAG",
            })
        for li in range(j1 + paired, j2):
            results.append({
                "id": lighton_tokens[li]["id"],
                "lighton_txt": l_words[li],
                "gemini_txt": None,
                "agree": False,
                "action": "FLAG",
            })
    return results


def run(gemini_dir: Path, lighton_dir: Path, out_path: Path | None = None) -> Path:
    gemini_pages = find_pages(gemini_dir)
    lighton_pages = find_pages(lighton_dir)
    common = sorted(set(gemini_pages) & set(lighton_pages))
    if not common:
        raise SystemExit(f"no overlapping page numbers between {gemini_dir} and {lighton_dir}")

    skipped = sorted(set(gemini_pages) ^ set(lighton_pages))
    if skipped:
        print(f"skipping {len(skipped)} page(s) only present on one side: {skipped}")

    out_path = out_path or (lighton_dir / "ensemble_review.json")
    # resumable: an existing report may already carry vlm_arbiter.py verdicts
    # (real, paid API calls) for pages compared earlier. Re-aligning those
    # pages here would silently discard that history, so only pages NOT
    # already in the report get (re-)computed -- new pages extend it, an
    # already-covered page is left exactly as it was.
    if out_path.exists():
        report = json.loads(out_path.read_text(encoding="utf-8"))
        already = {int(p) for p in report["pages"]}
        to_process = [n for n in common if n not in already]
        if already & set(common):
            print(f"{len(already & set(common))} already-compared page(s) left untouched "
                  f"(preserves any vlm_arbiter.py verdicts already recorded)")
    else:
        report = {"gemini_dir": str(gemini_dir), "lighton_dir": str(lighton_dir), "pages": {}}
        to_process = common

    for n in to_process:
        results = align_page(load_tokens(gemini_pages[n]), load_tokens(lighton_pages[n]))
        report["pages"][str(n)] = results

    total = sum(len(v) for v in report["pages"].values())
    flagged = sum(1 for v in report["pages"].values() for r in v if r["action"] == "FLAG")
    report["summary"] = {"pages_compared": len(report["pages"]), "tokens": total, "flagged": flagged}

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(to_process)} page(s) newly compared, {len(report['pages'])} total, "
          f"{flagged}/{total} tokens flagged overall -> {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Diff LightOn's OCR against a second engine's OCR, page by page.")
    ap.add_argument("gemini_dir", help="work/<book> dir from the reference engine (Gemini)")
    ap.add_argument("lighton_dir", help="work/<book>-lighton dir being audited")
    ap.add_argument("--out", default=None,
                     help="report path (default: <lighton_dir>/ensemble_review.json)")
    args = ap.parse_args()

    gemini_dir, lighton_dir = Path(args.gemini_dir), Path(args.lighton_dir)
    if not gemini_dir.is_dir():
        sys.exit(f"no such directory: {gemini_dir}")
    if not lighton_dir.is_dir():
        sys.exit(f"no such directory: {lighton_dir}")

    run(gemini_dir, lighton_dir, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
