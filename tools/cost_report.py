"""What did a book actually cost? (spec 025)

Reads work/<book>/usage.jsonl -- one line per paid provider call, written by the
pipeline drivers and tools since spec 025 -- and prices it with config/prices.toml.

Calls the price table can't price (a model not in it, LightOn/Hume until their plan
rates are filled in) are counted as "unpriced", never as $0, and the report says so.

Usage:
    python3 tools/cost_report.py "work/<book>" "work/<book>-lighton"
    python3 tools/cost_report.py "work/<book>-lighton" --by page
    python3 tools/cost_report.py "work/<book>" --by provider,model --json
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from pipeline.usage import read_log  # noqa: E402
from pipeline.usage_model import PriceTable, summarize  # noqa: E402

DEFAULT_PRICES = REPO_ROOT / "config" / "prices.toml"
GROUPINGS = {"operation", "stage", "page", "provider", "model"}


def run(work_dirs: list[Path], by: tuple[str, ...] = ("stage", "operation"),
        prices_path: Path = DEFAULT_PRICES, as_json: bool = False) -> dict:
    prices = PriceTable.from_toml(prices_path.read_text(encoding="utf-8"))
    records = [r for d in work_dirs for r in read_log(d)]
    lines = summarize(records, prices, by=by)
    total = summarize(records, prices, by=())[()] if records else None
    pages = {r.page for r in records if r.page is not None}

    report = {
        "prices_as_of": prices.as_of,
        "calls": len(records),
        "pages_with_calls": len(pages),
        "usd": round(total.usd, 6) if total else 0.0,
        "unpriced_calls": total.unpriced if total else 0,
        "usd_per_page": round(total.usd / len(pages), 6) if total and pages else None,
        "lines": [
            {**dict(zip(by, key)), "calls": ln.calls, "cached": ln.cached,
             "input_tokens": ln.input_tokens, "output_tokens": ln.output_tokens,
             "chars": ln.chars, "audio_ms": ln.audio_ms, "pages": ln.pages,
             "usd": round(ln.usd, 6), "unpriced": ln.unpriced}
            for key, ln in sorted(lines.items(), key=lambda kv: tuple(str(k) for k in kv[0]))
        ],
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print(report, by, work_dirs)
    return report


def _print(report: dict, by: tuple[str, ...], work_dirs: list[Path]) -> None:
    print(f"{', '.join(d.name for d in work_dirs)} -- prices as of {report['prices_as_of']}\n")
    if not report["calls"]:
        print("no usage recorded (usage.jsonl is written by runs made since spec 025)")
        return
    head = [*by, "calls", "in tok", "out tok", "chars", "audio s", "pages", "USD", "unpriced"]
    rows = [[*(str(ln.get(k, "-")) for k in by), str(ln["calls"]), f"{ln['input_tokens']:,}",
             f"{ln['output_tokens']:,}", f"{ln['chars']:,}", f"{ln['audio_ms'] / 1000:,.1f}",
             str(ln["pages"]), f"{ln['usd']:.4f}", str(ln["unpriced"] or "")]
            for ln in report["lines"]]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print(f"\n{report['calls']} paid call(s) across {report['pages_with_calls']} page(s): "
          f"${report['usd']:.4f}"
          + (f" (${report['usd_per_page']:.4f}/page)" if report["usd_per_page"] else ""))
    if report["unpriced_calls"]:
        print(f"WARNING: {report['unpriced_calls']} call(s) could not be priced -- "
              "the total above is a lower bound. Add their rates to config/prices.toml.")


def main():
    ap = argparse.ArgumentParser(description="Price a book's recorded provider usage.")
    ap.add_argument("work_dirs", nargs="+", help="book directories containing usage.jsonl")
    ap.add_argument("--by", default="stage,operation",
                    help=f"comma-separated grouping, from {sorted(GROUPINGS)}")
    ap.add_argument("--prices", default=str(DEFAULT_PRICES))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    by = tuple(f.strip() for f in args.by.split(",") if f.strip())
    if not set(by) <= GROUPINGS:
        sys.exit(f"--by must be from {sorted(GROUPINGS)}")
    run([Path(d) for d in args.work_dirs], by=by, prices_path=Path(args.prices), as_json=args.json)


if __name__ == "__main__":
    main()
