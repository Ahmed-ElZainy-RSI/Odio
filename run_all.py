"""Walk every «قيمي» book, show which stages are done, and run the ones that are not. Spec 035.

    python tools/qiyami/run_all.py                     # status only: what is done, what is missing (no changes)
    python tools/qiyami/run_all.py --run               # run every missing stage, paid OCR included
    python tools/qiyami/run_all.py --run --no-ocr      # everything except paid Gemini calls
    python tools/qiyami/run_all.py --run --book الثقة  # one book (repeat --book for more)
    python tools/qiyami/run_all.py --run --workers 8   # OCR 8 pages of a book at a time (same settings)

A book is a folder books/<publisher>/<book>/ holding exactly one PDF; the folder name is the book name
used everywhere else (work/qiyami/<book>/, result/<book>/, tools/qiyami/fixes/<book>.json).

Stages, in order (a stage runs only when the stages before it are done):
  1. ocr       every page has a saved Gemini run  -> qiyami_run.py --resume        (PAID, uploads pages to Google)
  2. layer     the text layer is diffed against the OCR -> check_textlayer.py --json (only for PDFs with a layer)
  3. review    tashkeel fixes decided: review sheets are made for a human (tashkeel_review.py); once the human
               has written work/qiyami/<book>/review_decisions.json, they are merged (--merge)
  4. build     result/<book>/ is complete and newer than the fixes and the OCR -> qiyami_run.py --rebuild
  5. audio     never run here: audio waits for management to approve the OCR

The human step (3) cannot be automated: the script makes the sheets, reports «waiting for review», and
merges the decisions on a later run. The Gemini key comes from $GEMINI_API_KEY or the repo's .env.
"""
import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BOOKS = REPO / "books"
WORK = REPO / "work" / "qiyami"
RESULT = REPO / "result"
FIXES = HERE / "fixes"
SHEETS = REPO / "work" / "review"
RAW = "Gemini-3.8-Flash.txt"
ARABIC = re.compile(r"[ء-ي]")

DONE, TODO, WAIT, SKIP, FAIL = "تم", "ناقص", "مستني مراجعة", "لا ينطبق", "فشل"


@dataclass
class Book:
    name: str
    pdf: Path
    pages: int
    has_layer: bool
    status: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def work(self) -> Path:
        return WORK / self.name

    @property
    def result(self) -> Path:
        return RESULT / self.name

    @property
    def fixes(self) -> Path:
        return FIXES / f"{self.name}.json"

    @property
    def diff(self) -> Path:
        return self.work / "textlayer_diff.json"

    @property
    def decisions(self) -> Path:
        return self.work / "review_decisions.json"

    @property
    def sheet(self) -> Path:
        return SHEETS / f"{self.name}_review.tsv"


def mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


def newest(paths) -> float:
    return max((mtime(p) for p in paths), default=0.0)


def find_books(only: list[str] | None) -> list[Book]:
    books = []
    for folder in sorted(p for p in BOOKS.glob("*/*") if p.is_dir()):
        if only and folder.name not in only:
            continue
        pdfs = sorted(folder.glob("*.pdf"))
        if len(pdfs) != 1:
            print(f"! {folder.relative_to(REPO)}: {len(pdfs)} PDF files (expected 1), skipped")
            continue
        with pymupdf.open(pdfs[0]) as doc:
            layer_pages = sum(bool(ARABIC.search(page.get_text())) for page in doc)
            books.append(Book(folder.name, pdfs[0], len(doc), has_layer=layer_pages > len(doc) // 2))
    if only:
        missing = set(only) - {b.name for b in books}
        for name in sorted(missing):
            print(f"! no book folder named {name!r} under books/")
    return books


# ── stage checks: each returns (state, note) without changing anything ─────────────────────────────

def raw_runs(b: Book) -> list[Path]:
    return [b.work / f"page_{n}" / RAW for n in range(1, b.pages + 1)]


def check_ocr(b: Book):
    done = sum(p.exists() for p in raw_runs(b))
    if done == b.pages:
        return DONE, ""
    return TODO, f"{done}/{b.pages} صفحة"


def check_layer(b: Book):
    if not b.has_layer:
        return SKIP, "مفيش text layer"
    if not b.diff.exists():
        return TODO, ""
    if mtime(b.diff) < newest(raw_runs(b)):
        return TODO, "الـ OCR أحدث من المقارنة"
    return DONE, ""


def check_review(b: Book):
    if not b.has_layer:
        return SKIP, "مفيش text layer" + (" (فيه تصحيحات يدوية)" if b.fixes.exists() else "")
    if b.decisions.exists():
        if mtime(b.fixes) >= mtime(b.decisions):
            return DONE, ""
        return TODO, "قرارات المراجعة لسه ما اتدمجتش"
    if b.fixes.exists():
        return DONE, "تصحيحات يدوية"
    if b.sheet.exists():
        return WAIT, f"اكتب القرارات في {b.decisions.relative_to(REPO)}"
    return TODO, "شيتات المراجعة لسه ما اتعملتش"


def check_build(b: Book):
    if not b.result.exists():
        return TODO, "result مش موجود"
    pages = len(list(b.result.glob("p[0-9][0-9][0-9].json")))
    if pages != b.pages:
        return TODO, f"{pages}/{b.pages} صفحة في result"
    for f in ("book.json", "ocr_check.html", "reader.html"):
        if not (b.result / f).exists():
            return TODO, f"{f} ناقص"
    built = mtime(b.result / "book.json")
    if built < mtime(b.fixes):
        return TODO, "التصحيحات أحدث من result"
    if built < newest(raw_runs(b)):
        return TODO, "الـ OCR أحدث من result"
    return DONE, ""


def check_audio(b: Book):
    if (b.result / "audio").exists():
        return DONE, ""
    return SKIP, "مستني اعتماد الإدارة"


STAGES = [("ocr", check_ocr), ("layer", check_layer), ("review", check_review),
          ("build", check_build), ("audio", check_audio)]


def refresh(b: Book):
    b.status = {name: check(b) for name, check in STAGES}


# ── running ──────────────────────────────────────────────────────────────────────────────────────

def gemini_key() -> str | None:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*GEMINI_API_KEY\s*=\s*(.*)$", line)
            if m:
                return m.group(1).split(" #")[0].strip().strip("\"'") or None
    return None


def run(b: Book, script: str, *args: str, key: str | None = None, log=None) -> bool:
    cmd = [sys.executable, str(HERE / script), *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if key:
        env["GEMINI_API_KEY"] = key
    print(f"  → {script} {' '.join(args)}", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, env=env, text=True, encoding="utf-8", errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log:
        log.write(f"\n===== {b.name}: {script} {' '.join(args)} (exit {proc.returncode}) =====\n{proc.stdout}")
        log.flush()
    tail = proc.stdout.strip().splitlines()[-3:]
    for line in tail:
        print(f"    {line}")
    if proc.returncode != 0:
        b.notes.append(f"{script} فشل (exit {proc.returncode})، التفاصيل في اللوج")
    return proc.returncode == 0


def run_ocr_parallel(b: Book, workers: int, key: str, log) -> None:
    """OCR the missing pages with `workers` qiyami_run.py processes, each on its own pages.

    Same settings as one process (5 samples, zoom strips, vote), so the text is the same; only the pages run
    side by side. Pages are dealt round-robin so every worker gets early and late pages alike. Workers skip
    the result/ build (--no-build): the build stage below does it once, after all of them.
    """
    missing = [n for n, p in enumerate(raw_runs(b), 1) if not p.exists()]
    workers = max(1, min(workers, len(missing)))
    shares = [missing[i::workers] for i in range(workers)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "GEMINI_API_KEY": key}
    print(f"  → qiyami_run.py × {workers} ({len(missing)} صفحة ناقصة)", flush=True)
    logs_dir = b.work / "workers"
    logs_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for i, pages in enumerate(shares, 1):
        out = (logs_dir / f"worker_{i:02d}.log").open("w", encoding="utf-8")
        cmd = [sys.executable, str(HERE / "qiyami_run.py"), "--pdf", str(b.pdf), "--book", b.name,
               "--resume", "--no-build", "--pages", *map(str, pages)]
        procs.append((i, out, subprocess.Popen(cmd, cwd=REPO, env=env, stdout=out, stderr=subprocess.STDOUT)))
    failed = []
    for i, out, proc in procs:
        proc.wait()
        out.close()
        if proc.returncode != 0:
            failed.append(i)
    if log:
        for i, _, proc in procs:
            text = (logs_dir / f"worker_{i:02d}.log").read_text(encoding="utf-8", errors="replace")
            log.write(f"\n===== {b.name}: worker {i} (exit {proc.returncode}) =====\n{text}")
        log.flush()
    if failed:
        b.notes.append(f"{len(failed)} من {workers} عملية فشلت، التفاصيل في {logs_dir.relative_to(REPO)}")


def process(b: Book, *, ocr: bool, key: str | None, log, workers: int = 1) -> None:
    refresh(b)
    pdf = str(b.pdf)

    if b.status["ocr"][0] == TODO:
        if not ocr:
            b.notes.append("الـ OCR اتخطّى (--no-ocr)")
            return
        if not key:
            b.notes.append("الـ OCR محتاج GEMINI_API_KEY")
            return
        if workers > 1:
            run_ocr_parallel(b, workers, key, log)
        # --resume calls the API only for pages with no saved run; it also builds result/<book>/
        elif not run(b, "qiyami_run.py", "--pdf", pdf, "--book", b.name, "--resume", key=key, log=log):
            return
        refresh(b)
        if b.status["ocr"][0] != DONE:
            return

    if b.status["layer"][0] == TODO:
        if not run(b, "check_textlayer.py", "--pdf", pdf, "--book", b.name, "--json", str(b.diff), log=log):
            return
        refresh(b)

    state, _ = b.status["review"]
    if state == TODO:
        if b.decisions.exists():
            if not run(b, "tashkeel_review.py", "--pdf", pdf, "--book", b.name, "--merge", log=log):
                return
        elif b.diff.exists():
            run(b, "tashkeel_review.py", "--pdf", pdf, "--book", b.name, "--sheets", str(SHEETS), log=log)
        refresh(b)
    # a book waiting for its human review is still built, so the reader shows the current text

    if b.status["build"][0] == TODO:
        run(b, "qiyami_run.py", "--pdf", pdf, "--book", b.name, "--rebuild", log=log)
        refresh(b)


def report(books: list[Book]) -> None:
    width = max((len(b.name) for b in books), default=10) + 2
    print("\n" + "الكتاب".ljust(width) + "  ".join(f"{s:<10}" for s, _ in STAGES))
    for b in books:
        cells = "  ".join(f"{b.status[s][0]:<10}" for s, _ in STAGES)
        print(f"{b.name:<{width}}{cells}")
        for s, _ in STAGES:
            state, note = b.status[s]
            if note and state not in (DONE, SKIP):
                print(f"{'':<{width}}  {s}: {note}")
        for note in b.notes:
            print(f"{'':<{width}}  ! {note}")
    todo = [b for b in books if any(b.status[s][0] == TODO for s, _ in STAGES)]
    wait = [b for b in books if b.status["review"][0] == WAIT]
    print(f"\n{len(books)} كتاب: {len(books) - len(todo) - len(wait)} مكتمل، "
          f"{len(todo)} فيه مراحل ناقصة، {len(wait)} مستني مراجعة يدوية.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="store_true", help="run the missing stages (default: status only)")
    ap.add_argument("--no-ocr", action="store_true", help="never call Gemini; skip books whose OCR is missing")
    ap.add_argument("--book", action="append", help="only this book (folder name); repeatable")
    ap.add_argument("--workers", type=int, default=1,
                    help="OCR a book's missing pages with N processes side by side (same settings, same cost)")
    a = ap.parse_args()

    books = find_books(a.book)
    for b in books:
        refresh(b)

    if not a.run:
        report(books)
        missing = sum(b.pages - sum(p.exists() for p in raw_runs(b)) for b in books)
        if missing:
            print(f"OCR ناقص لـ {missing} صفحة (مدفوع على Gemini). شغّل بـ --run للتنفيذ، أو --run --no-ocr من غيره.")
        return

    key = None if a.no_ocr else gemini_key()
    log_path = WORK / "run_all.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        for b in books:
            if all(b.status[s][0] in (DONE, SKIP, WAIT) for s, _ in STAGES):
                continue
            print(f"\n■ {b.name} ({b.pages} صفحة)", flush=True)
            process(b, ocr=not a.no_ocr, key=key, log=log, workers=a.workers)
            refresh(b)  # a stage that failed returns early: report what is on disk now, not before the run
    report(books)
    print(f"اللوج الكامل: {log_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
