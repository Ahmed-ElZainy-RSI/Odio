"""Offline step 3: write VLM-verified corrections from ensemble_review.json
into the actual pXXX.json token records LightOn's pipeline reads.

The first tool to touch pXXX.json in this chain -- ensemble_review.py and
vlm_arbiter.py both only read/report. Every write here is reversible: the
pre-correction text is kept in "ocr_txt" and "ensemble-vlm" is appended to
"fixes", the same pattern extract.py's clean_word already uses to record
what changed and why.

Two categories are deliberately NOT auto-applied, and are reported instead
of guessed at:
  - verified_txt == "" -- the VLM decided the word doesn't exist on the
    page at all. Applying that means deleting a token (renumbering ids,
    invalidating box math), which is a bigger structural change than a
    text substitution and wasn't something this POC decided how to do.
  - "gemini-only:..." ids -- Gemini saw a word LightOn dropped entirely.
    There is no LightOn token to attach the correction to; inserting a new
    one raises the same id/box questions as the deletion case above.

A page that gets any correction has its status reset to "extracted" so
run_tashkel.py / run_speak_*.py naturally redo it -- their own resumability
checks skip anything already 'diacritised' or 'spoken', which would
otherwise leave stale audio narrating the pre-correction word.

Usage:
    python3 tools/apply_corrections.py "work/<book>-lighton/ensemble_review.json"
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

    by_page: dict[int, list[dict]] = {}
    skipped_empty = skipped_insert = 0
    for results in review["pages"].values():
        for r in results:
            if r["action"] != "VERIFIED":
                continue
            if r["id"].startswith("gemini-only:"):
                skipped_insert += 1
                continue
            if r["verified_txt"] == "":
                skipped_empty += 1
                continue
            m = PAGE_FROM_ID.match(r["id"])
            by_page.setdefault(int(m.group(1)), []).append(r)

    applied = 0
    for page_no, corrections in sorted(by_page.items()):
        page_path = lighton_dir / f"p{page_no:03d}.json"
        rec = json.loads(page_path.read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in rec["tokens"]}

        changed = False
        for r in corrections:
            tok = by_id.get(r["id"])
            if tok is None or tok["txt"] == r["verified_txt"]:
                continue
            tok["ocr_txt"] = tok["txt"]
            tok["txt"] = r["verified_txt"]
            tok["fixes"] = tok["fixes"] + ["ensemble-vlm"]
            applied += 1
            changed = True

        if changed:
            rec["status"] = "extracted"
            page_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"page {page_no}: {len(corrections)} correction(s) applied, status reset")

    print(f"\n{applied} tokens corrected across {len(by_page)} page(s)")
    if skipped_empty:
        print(f"{skipped_empty} skipped (VLM found no word there -- needs a manual delete decision)")
    if skipped_insert:
        print(f"{skipped_insert} skipped (Gemini-only word LightOn never produced -- needs a manual insert decision)")
    if applied:
        print(f"\nre-run diacritise/TTS/reader for this book, e.g.:\n"
              f'  python3 -c "import sys; sys.path.insert(0,\'src\'); '
              f"from pipeline import run_tashkel, run_speak_gemini, build_reader; "
              f"from pathlib import Path; d=Path({str(lighton_dir)!r}); "
              f'run_tashkel.run(d); run_speak_gemini.run(d, run_speak_gemini.VOICE); build_reader.build(d)"')


def main():
    ap = argparse.ArgumentParser(
        description="Apply VLM-verified corrections from ensemble_review.json into pXXX.json.")
    ap.add_argument("review_json", help="path to ensemble_review.json")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path)


if __name__ == "__main__":
    main()
