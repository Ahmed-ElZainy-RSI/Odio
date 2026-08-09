"""Layer 04b for one book: diacritise pages routed 'diacritise'.

Pages routed 'keep' (already vocalised) are left untouched. Resumable: a page
already marked status 'diacritised' is skipped, so a mid-book failure costs
at most one page on retry.
"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.tashkel import diacritize_tokens

BOOK = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك ")
WORK = Path(f"work/{BOOK.strip()}")


def main():
    pages = sorted(WORK.glob("p*.json"))
    print(f"{BOOK.strip()} — {len(pages)} pages\n")
    print(f"{'pg':>3}  path         result")
    print("-" * 50)

    done = skipped = mismatched = 0
    for p in pages:
        rec = json.loads(p.read_text(encoding="utf-8"))
        page = rec.get("page", "?")

        if rec.get("path") != "diacritise":
            print(f"{page:>3}  {rec.get('path', '?'):<11}  already vocalised, skipped")
            skipped += 1
            continue
        if rec.get("status") == "diacritised":
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


if __name__ == "__main__":
    main()
