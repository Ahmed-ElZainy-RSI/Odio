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
from pipeline import failures
from pipeline.tashkel import diacritize_tokens


def run(work_dir: Path):
    """Diacritise every page in work_dir that's routed 'diacritise' and not already done.

    No page-range option here on purpose: --pages/PAGES only controls which
    pages get fetched from the PDF (extraction/OCR). Once pages are on disk,
    this processes everything that still needs it, so a run also catches up
    any earlier pages left un-diacritised -- status already prevents redundant
    work, a second range filter on top would only make that backlog invisible.
    """
    all_pages = sorted(work_dir.glob("p*.json"))
    print(f"{work_dir.name} — {len(all_pages)} pages\n")
    print(f"{'pg':>3}  path         result")
    print("-" * 50)

    done = skipped = mismatched = rejected = 0
    for p in all_pages:
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

        try:
            tokens, report = diacritize_tokens(rec["tokens"])
        except Exception as e:
            # local model, so no quota: a crash on one page is recorded and the
            # rest of the book still gets diacritised (spec 008)
            failures.handle(work_dir, page, "diacritise", e, f"{page:>3}")
            continue
        failures.clear(work_dir, page, "diacritise")
        if report["mismatch"]:
            print(f"{page:>3}  diacritise   MISMATCH expected {report['expected']} "
                  f"got {report['got']} tokens — left untouched")
            mismatched += 1
            continue
        if not report["preserved"]:
            # The diacritiser changed a letter, not just marks. Keep the page as it was
            # and record where, so it can be checked by hand; status stays put, so the
            # next run tries again.
            diff = report["first_diff"]
            rec["diac_error"] = diff
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{page:>3}  diacritise   REJECTED {diff['id']} {diff['before']} -> "
                  f"{diff['after']} ({diff['reason']}) — left untouched")
            rejected += 1
            continue

        rec["tokens"] = tokens
        rec["status"] = "diacritised"
        rec["tashkeel_engine"] = "arabic-diacritizer"
        rec.pop("diac_error", None)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{page:>3}  diacritise   {report['diacritized']} tokens diacritised")
        done += 1

    print(f"\n{done} diacritised, {skipped} skipped, {mismatched} mismatched, "
          f"{rejected} rejected (letters changed)")
    return done, skipped, mismatched, rejected


def main():
    book = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
    run(Path(f"work/{book.strip()}"))


if __name__ == "__main__":
    main()
