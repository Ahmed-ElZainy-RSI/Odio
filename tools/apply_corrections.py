"""Offline step 3: write VLM-verified corrections from ensemble_review.json
into the actual pXXX.json token records LightOn's pipeline reads.

The first tool to touch pXXX.json in this chain -- ensemble_review.py and
vlm_arbiter.py both only read/report. Every write here is reversible: the
pre-correction text is kept in "ocr_txt" and "ensemble-vlm" is appended to
"fixes", the same pattern extract.py's clean_word already uses to record
what changed and why.

verified_txt == "" (the VLM decided the word doesn't exist on the page at
all) is handled by removing the token outright -- safe because nothing
downstream indexes by id: the reader walks tokens[] by array position
(dataset.j), and diacritize_tokens() re-matches by position against
whatever's in the list *after* this runs, not against the id string. A
line's word-box proportions were computed including the now-deleted word
and don't get recomputed, so remaining boxes on that line stay
approximate -- box_level "line" already documents them as such.

"gemini-only:..." ids are NOT auto-applied. Gemini saw a word LightOn
dropped entirely, but inserting one means picking a position, minting an
id, and (on box_level "line" pages) sourcing a box -- and when these
cluster (see tools/ensemble_review.py's summary), it usually means LightOn
failed the page wholesale, not that a single word is missing. That's a
different fix (replace the page's LightOn tokens with Gemini's) than
per-token insertion, and this POC doesn't decide that automatically --
reported instead, grouped by page, so a human can see which.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ensemble_review import normalize_for_compare  # noqa: E402

PAGE_FROM_ID = re.compile(r"^p(\d+)w\d+$")


def run(review_path: Path) -> None:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent

    by_page: dict[int, list[dict]] = {}
    inserts_by_page: dict[int, int] = {}
    for results in review["pages"].values():
        for r in results:
            if r["action"] != "VERIFIED":
                continue
            if r["id"].startswith("gemini-only:"):
                page = int(PAGE_FROM_ID.match(r["id"].split(":", 1)[1]).group(1))
                inserts_by_page[page] = inserts_by_page.get(page, 0) + 1
                continue
            m = PAGE_FROM_ID.match(r["id"])
            by_page.setdefault(int(m.group(1)), []).append(r)

    corrected = deleted = 0
    for page_no, corrections in sorted(by_page.items()):
        page_path = lighton_dir / f"p{page_no:03d}.json"
        rec = json.loads(page_path.read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in rec["tokens"]}

        to_delete = set()
        page_corrected = 0
        for r in corrections:
            tok = by_id.get(r["id"])
            if tok is None:
                continue
            if r["verified_txt"] == "":
                to_delete.add(r["id"])
                continue
            # verified_txt often comes back undiacritized even when tok["txt"]
            # already has correct diacritics from an earlier run_tashkel pass
            # -- an exact-string check would treat that as a mismatch, strip
            # the diacritics back off, and force a wasted re-diacritize +
            # re-TTS cycle on a page that needed no actual correction. Only
            # the letters need to differ for this to be a real fix.
            if normalize_for_compare(tok["txt"]) == normalize_for_compare(r["verified_txt"]):
                continue
            tok["ocr_txt"] = tok["txt"]
            tok["txt"] = r["verified_txt"]
            tok["fixes"] = tok["fixes"] + ["ensemble-vlm"]
            page_corrected += 1

        if to_delete:
            rec["tokens"] = [t for t in rec["tokens"] if t["id"] not in to_delete]

        if page_corrected or to_delete:
            corrected += page_corrected
            deleted += len(to_delete)
            rec["status"] = "extracted"
            page_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"page {page_no}: {page_corrected} correction(s), "
                  f"{len(to_delete)} deletion(s), status reset")

    print(f"\n{corrected} tokens corrected, {deleted} deleted, across {len(by_page)} page(s)")
    if inserts_by_page:
        total_inserts = sum(inserts_by_page.values())
        print(f"{total_inserts} gemini-only words not applied (no LightOn token to attach to) -- by page:")
        for page, n in sorted(inserts_by_page.items(), key=lambda x: -x[1]):
            print(f"  page {page}: {n}")
    if corrected or deleted:
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
