"""Offline step 6: apply only the context_detector.py suggestions a human
explicitly accepted in context_decisions.json.

Nothing here is automatic. context_decisions.json defaults every new
suspicion to "reject" (see context_detector.py) -- a reviewer must change a
value to "accept" (use the LLM's proposed word) or to any other literal
string (use that instead) before this tool will touch anything. That's the
whole point of keeping this a separate layer from apply_corrections.py's
VERIFIED tier: a context-only guess has no image behind it, so it never
gets the same automatic trust a vlm_arbiter.py verdict does.

Same reversible-write pattern as apply_corrections.py: the pre-suggestion
word is kept in ocr_txt (or left alone if apply_corrections.py already set
it earlier), and "context-llm" is appended to fixes[]. A token whose
current txt no longer matches the ocr_word recorded at detection time is
skipped and reported rather than blindly overwritten -- something else
(another correction pass, a manual edit) touched it since, and this tool
has no way to know which change should win.

Usage:
    python3 tools/apply_context_suggestions.py "work/<book>-lighton/context_review.json"
"""
import argparse
import json
import re
import sys
from pathlib import Path

PAGE_FROM_ID = re.compile(r"^p(\d+)w\d+$")


def run(review_path: Path) -> None:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent
    decisions_path = lighton_dir / "context_decisions.json"
    if not decisions_path.exists():
        raise SystemExit(f"no decisions file at {decisions_path} -- run context_detector.py first")
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))

    by_page: dict[int, list[tuple[dict, str]]] = {}
    for entries in review["pages"].values():
        for e in entries:
            decision = decisions.get(e["id"], "reject")
            if decision == "reject":
                continue
            new_txt = e["proposed"] if decision == "accept" else decision
            m = PAGE_FROM_ID.match(e["id"])
            by_page.setdefault(int(m.group(1)), []).append((e, new_txt))

    applied = skipped_stale = 0
    for page_no, accepted in sorted(by_page.items()):
        page_path = lighton_dir / f"p{page_no:03d}.json"
        rec = json.loads(page_path.read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in rec["tokens"]}

        changed = False
        for e, new_txt in accepted:
            tok = by_id.get(e["id"])
            if tok is None:
                continue
            if tok["txt"] != e["ocr_word"]:
                print(f"page {page_no}: {e['id']} changed since detection "
                      f"({e['ocr_word']!r} -> now {tok['txt']!r}), skipping")
                skipped_stale += 1
                continue
            if "ocr_txt" not in tok:
                tok["ocr_txt"] = tok["txt"]
            tok["txt"] = new_txt
            tok["fixes"] = tok["fixes"] + ["context-llm"]
            applied += 1
            changed = True

        if changed:
            rec["status"] = "extracted"
            page_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"page {page_no}: {sum(1 for _ in accepted)} accepted suggestion(s) applied, status reset")

    print(f"\n{applied} tokens corrected, {skipped_stale} skipped (changed since detection)")
    if applied:
        print("re-run diacritise/TTS/reader for this book to pick up the changes")


def main():
    ap = argparse.ArgumentParser(
        description="Apply accepted context_detector.py suggestions from context_decisions.json.")
    ap.add_argument("review_json", help="path to context_review.json")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path)


if __name__ == "__main__":
    main()
