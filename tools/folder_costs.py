"""What did a folder run cost, per page and per book? Writes the publisher's cost_per_page.csv.

Reads usage.jsonl in every book folder of a results folder (by default the run_folder.py output,
result/ocr_results/) and prices it with config/prices.toml, batch calls at batch rates. Free: no
API calls.

    python3 tools/folder_costs.py "دار المستقبل"                     # result/ocr_results/*
    python3 tools/folder_costs.py "دار المستقبل" --books "$RES/دار المستقبل"   # what's in OCR Results

Prints one line per book and the total, and writes result/runs/<publisher>/cost_per_page.csv
(book, page, model, calls, input_tokens, output_tokens, usd), UTF-8 with BOM so Excel shows Arabic.
"""
import argparse
import csv
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from pipeline.usage import read_log  # noqa: E402
from pipeline.usage_model import PriceTable, summarize  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Per-page and per-book cost of a folder run.")
    ap.add_argument("publisher", help="names the output folder result/runs/<publisher>/")
    ap.add_argument("--books", type=Path, default=REPO_ROOT / "result" / "ocr_results",
                    help="folder holding one folder per book (default: result/ocr_results)")
    ap.add_argument("--out", type=Path, help="CSV path (default: result/runs/<publisher>/cost_per_page.csv)")
    a = ap.parse_args()

    prices = PriceTable.from_toml((REPO_ROOT / "config" / "prices.toml").read_text(encoding="utf-8"))
    books = sorted(d for d in a.books.iterdir() if d.is_dir() and (d / "usage.jsonl").exists())
    if not books:
        sys.exit(f"No book folders with usage.jsonl in {a.books}")

    rows, total, pages, unpriced = [], 0.0, 0, 0
    width = max(len(d.name) for d in books)
    print(f"{'book'.ljust(width)}  pages  USD      per page")
    for d in books:
        name = unicodedata.normalize("NFC", d.name)
        lines = summarize(read_log(d), prices, by=("page", "model"))
        usd = sum(ln.usd for ln in lines.values())
        n = len({k[0] for k in lines})
        for (page, model), ln in sorted(lines.items(), key=lambda kv: (kv[0][0] or 0, str(kv[0][1]))):
            rows.append([name, page, model, ln.calls, ln.input_tokens, ln.output_tokens, round(ln.usd, 6)])
            unpriced += ln.unpriced
        total, pages = total + usd, pages + n
        print(f"{name.ljust(width)}  {n:>5}  ${usd:.4f}  ${usd / n:.5f}" if n else f"{name}: no calls")

    out = a.out or REPO_ROOT / "result" / "runs" / a.publisher / "cost_per_page.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["book", "page", "model", "calls", "input_tokens", "output_tokens", "usd"])
        w.writerows(rows)
    print(f"\n{len(books)} books, {pages} pages: ${total:.4f}"
          + (f" (${total / pages:.5f} a page)" if pages else ""))
    if unpriced:
        print(f"WARNING: {unpriced} call(s) could not be priced; the total is a lower bound.")
    print(f"Per-page detail: {out}")


if __name__ == "__main__":
    main()
