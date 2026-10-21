"""Run a folder of PDFs through the pipeline, several books at a time (spec 015).

    python3 src/pipeline/batch.py "assets/PDF Samples" --workers 4
    python3 src/pipeline/batch.py "assets/PDF Samples" --workers 4 --ocr lighton --pages 5

One process per book (never two processes on one book: page records have a single writer).
Books with a text layer go first -- extraction is free, so they reach TTS sooner -- then
the scanned ones. Every book resumes exactly as main.py does (specs 008/009/013), so a
batch interrupted by Ctrl-C or a quota wall is simply run again.

Parallel workers share free-tier quotas: set PIPELINE_RATE_LIMITS (ratelimit.py) so they
stop together at the limit instead of each burning the allowance on failing calls.
"""
import argparse
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import main as pipeline_main


def discover(folder: Path) -> list[Path]:
    """PDFs in folder, text-layer books first, then scanned; alphabetical within each."""
    pdfs = sorted(Path(folder).glob("*.pdf"))
    def has_layer(p: Path) -> bool:
        try:
            return pipeline_main.has_text_layer(p)
        except Exception:  # noqa: BLE001 -- an unreadable PDF just sorts last; its run reports it
            return False
    flags = {p: has_layer(p) for p in pdfs}
    return sorted(pdfs, key=lambda p: (not flags[p], p.name))


def _run_one(runner: Callable, pdf: Path, options: dict) -> dict:
    """In the worker process: one book, never raising (the summary carries the error)."""
    try:
        out = runner(pdf, **options)
        return {"book": pdf.name, "ok": True, "reader": str(out) if out else None}
    except Exception as e:  # noqa: BLE001 -- one book's failure must not stop the batch
        return {"book": pdf.name, "ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                "trace": traceback.format_exc(limit=3)}


def run(folder: Path, *, workers: int = 2, runner: Callable | None = None,
        **options) -> list[dict]:
    """Process every PDF in folder. workers=1 runs in this process (no pool)."""
    runner = runner or pipeline_main.run_pipeline
    books = discover(folder)
    print(f"{len(books)} book(s) in {folder}, {workers} worker(s)\n")
    results: list[dict] = []
    if workers <= 1:
        results = [_run_one(runner, pdf, options) for pdf in books]
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        try:
            futures = {pool.submit(_run_one, runner, pdf, options): pdf for pdf in books}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"[{len(results)}/{len(books)}] {result['book']}: "
                      f"{'done' if result['ok'] else 'FAILED ' + result['error']}")
        except KeyboardInterrupt:
            print("\ninterrupted -- finishing the books already running, cancelling the rest; "
                  "re-run to resume")
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True)
    order = {pdf.name: i for i, pdf in enumerate(books)}
    results.sort(key=lambda r: order[r["book"]])
    _summary(results)
    return results


def _summary(results: list[dict]) -> None:
    ok = [r for r in results if r["ok"]]
    print(f"\n{len(ok)}/{len(results)} book(s) finished without an exception")
    for r in results:
        if not r["ok"]:
            print(f"  FAILED {r['book']}: {r['error']}")
    print("per-page failures, if any, are in each book's work/<book>/failures.json")


def main():
    ap = argparse.ArgumentParser(description="Run every PDF in a folder through the pipeline.")
    ap.add_argument("folder")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--ocr", choices=["gemini", "lighton"], default="gemini")
    ap.add_argument("--tts", choices=["gemini", "hume"], default="gemini")
    ap.add_argument("--pages", default="0")
    args = ap.parse_args()
    results = run(Path(args.folder), workers=args.workers, ocr=args.ocr, tts=args.tts,
                  pages=args.pages)
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
