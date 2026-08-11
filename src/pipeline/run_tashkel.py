"""Layer 04b for one book: diacritise pages routed 'diacritise'.

Pages routed 'keep' (already vocalised) are left untouched. Resumable: a page
already past this stage is skipped, so a mid-book failure costs at most one
page on retry. "Past this stage" means status 'diacritised' *or* anything
later in the pipeline (currently just 'spoken') -- checking only the literal
string 'diacritised' once clobbered 13 already-spoken pages' status back a
stage on a re-run, silently dropping their audio from build_reader.py's
'spoken'-only filter even though the audio and bind_report were still on
disk untouched.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.tashkel import diacritize_tokens


def run(work_dir: Path, pages: int = 0):
    """Diacritise pages in work_dir (first `pages` by page number, 0 = all present)."""
    all_pages = sorted(work_dir.glob("p*.json"))
    page_files = all_pages[:pages] if pages else all_pages
    print(f"{work_dir.name} — {len(page_files)} of {len(all_pages)} pages\n")
    print(f"{'pg':>3}  path         result")
    print("-" * 50)

    done = skipped = mismatched = 0
    for p in page_files:
        rec = json.loads(p.read_text(encoding="utf-8"))
        page = rec.get("page", "?")

        if rec.get("path") != "diacritise":
            print(f"{page:>3}  {rec.get('path', '?'):<11}  already vocalised, skipped")
            skipped += 1
            continue
        if rec.get("status") in ("diacritised", "spoken"):
            print(f"{page:>3}  diacritise   already done, skipping")
            skipped += 1
            continue

        tokens, report = diacritize_tokens(rec["tokens"])
        if report["mismatch"]:
            print(f"{page:>3}  diacritise   MISMATCH expected {report['expected']} "
                  f"got {report['got']} tokens — left untouched")
            mismatched += 1
            continue

        rec["tokens"] = tokens
        rec["status"] = "diacritised"
        rec["tashkeel_engine"] = "arabic-diacritizer"
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{page:>3}  diacritise   {report['diacritized']} tokens diacritised")
        done += 1

    print(f"\n{done} diacritised, {skipped} skipped, {mismatched} mismatched")
    return done, skipped, mismatched


def main():
    book = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
    run(Path(f"work/{book.strip()}"), pages=int(os.environ.get("PAGES", "0")))


if __name__ == "__main__":
    main()
