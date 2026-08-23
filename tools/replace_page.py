"""Offline step 4: wholesale page replacement for the case
apply_corrections.py deliberately doesn't handle -- a page where LightOn
didn't misread a few words, it failed to read the page at all.

tools/ensemble_review.py already tells you which pages these are: when
most/all of a page's tokens are "gemini-only" (Gemini has a word, LightOn
never produced anything at all), per-token insertion means guessing
position, id and box for dozens of words -- at that point the honest fix
isn't inserting words one at a time, it's recognising LightOn's read of
THAT PAGE is unusable and taking Gemini's instead. Gemini tokens already
share LightOn's exact schema (id/raw/txt/fixes/box, from ocr_gemini.py's
lines_to_tokens -> extract.py's clean_word), so this is a straight swap,
not a format conversion.

Only replaces pages at or above --threshold fraction gemini-only (default
0.5) so it can't silently swap a page that just had a few real
disagreements -- apply_corrections.py already handles those correctly.

Usage:
    python3 tools/replace_page.py "work/<book>-lighton/ensemble_review.json"
    python3 tools/replace_page.py "work/<book>-lighton/ensemble_review.json" --page 16
    python3 tools/replace_page.py "work/<book>-lighton/ensemble_review.json" --threshold 0.7
"""
import argparse
import json
import sys
from pathlib import Path


def find_candidates(review: dict, threshold: float) -> list[int]:
    candidates = []
    for page_str, results in review["pages"].items():
        if not results:
            continue
        gemini_only = sum(1 for r in results if r["id"].startswith("gemini-only:"))
        if gemini_only / len(results) >= threshold:
            candidates.append(int(page_str))
    return sorted(candidates)


def replace_page(lighton_dir: Path, gemini_dir: Path, page_no: int) -> bool:
    lighton_path = lighton_dir / f"p{page_no:03d}.json"
    gemini_path = gemini_dir / f"p{page_no:03d}.json"
    if not gemini_path.exists():
        print(f"page {page_no}: no Gemini page at {gemini_path}, skipping")
        return False

    lighton_rec = json.loads(lighton_path.read_text(encoding="utf-8"))
    gemini_rec = json.loads(gemini_path.read_text(encoding="utf-8"))

    lighton_rec["tokens"] = gemini_rec["tokens"]
    lighton_rec["box_level"] = gemini_rec.get("box_level", "line")
    lighton_rec["ocr_engine_replaced_from"] = lighton_rec.get("ocr_engine")
    lighton_rec["ocr_engine"] = gemini_rec.get("ocr_engine", "gemini")
    lighton_rec["status"] = "extracted"
    lighton_path.write_text(json.dumps(lighton_rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"page {page_no}: replaced {len(lighton_rec['tokens'])} tokens from Gemini, status reset")
    return True


def run(review_path: Path, threshold: float, only_page: int | None) -> None:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    lighton_dir = review_path.parent
    gemini_dir = Path(review["gemini_dir"])

    pages = [only_page] if only_page else find_candidates(review, threshold)
    if not pages:
        print(f"no pages at or above {threshold:.0%} gemini-only")
        return

    replaced = [p for p in pages if replace_page(lighton_dir, gemini_dir, p)]
    if replaced:
        print(f"\n{len(replaced)} page(s) replaced: {replaced}")
        print("re-run diacritise/TTS/reader for this book to pick up the swap")


def main():
    ap = argparse.ArgumentParser(
        description="Replace whole pages where LightOn failed with Gemini's tokens for the same page.")
    ap.add_argument("review_json", help="path to ensemble_review.json")
    ap.add_argument("--page", type=int, default=None, help="replace only this page number")
    ap.add_argument("--threshold", type=float, default=0.5,
                     help="min fraction of gemini-only tokens to auto-select a page (default 0.5)")
    args = ap.parse_args()

    review_path = Path(args.review_json)
    if not review_path.exists():
        sys.exit(f"no such file: {review_path}")
    run(review_path, args.threshold, args.page)


if __name__ == "__main__":
    main()
