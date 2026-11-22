"""OCR a whole folder of books and lay the results out like SharePoint «OCR Results».

Point it at any folder of books: a publisher folder in the synced «Reading First Book Sample»,
or a local folder. Books are found with the same rules as the benchmark (tools/benchmark.py):
one book per interior PDF, covers never counted, a loose copy of a book folder's PDF once.

For each book it runs this repo's pipeline (src/pipeline/main.py):
  default   Gemini OCR -> ocr_check.html (the Arabic reviewers' page). No diacritics, no audio.
  --full    Gemini OCR -> diacritics -> reader.html -> ocr_check.html. Still no audio: audio
            waits until management approves the OCR.

Results land in result/ocr_results/<book>/, one folder per book, the same files as a book in
«OCR Results»: book.json, p001.json…, pages/, check_pages/, ocr_check.html, (reader.html),
timings.jsonl, usage.jsonl. Nothing is uploaded unless you pass --upload.

    # 1. see what it would do: books found, which are already delivered, nothing paid
    python tools/run_folder.py "<folder>" --dry-run

    # 2. try it cheaply on the first 2 pages of one book
    python tools/run_folder.py "<folder>" --book "النظافة" --pages 2

    # 3. run the folder (books already in «OCR Results» are skipped)
    python tools/run_folder.py "<folder>"

    # 4. copy the finished books into the synced «OCR Results»/<publisher>/<book>/; OneDrive uploads them
    python tools/run_folder.py "<folder>" --upload

Every book's PDF is copied once to work/_sources/ (git-ignored) and its pages counted: a PDF
with 1-2 pages is a cover and is skipped. The counts are saved to result/page_counts.json so the
benchmark dashboard leaves those covers out too.

Every stage resumes: a run stopped by a Gemini quota, a crash or Ctrl-C picks up where it
stopped when you run the same command again, without paying for finished pages twice.

Costs money: each OCR'd page is one paid Gemini call (about $0.006 a page in this repo's logs)
and uploads the page image to Google. Reading a PDF from an online-only OneDrive folder
downloads that PDF to this Mac.

Needs the vx2lm conda env (pymupdf, google-genai) and GEMINI_API_KEY in .env or the environment:
    ~/miniconda3/envs/vx2lm/bin/python tools/run_folder.py "<folder>" --dry-run
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "src"))

import benchmark as bm  # noqa: E402  book finding, title matching, OneDrive lookup

try:
    import pymupdf  # noqa: E402
except ImportError:
    sys.exit("pymupdf is missing: run this with the vx2lm env, e.g.\n"
             "  ~/miniconda3/envs/vx2lm/bin/python tools/run_folder.py ...")

OUT = REPO / "result" / "ocr_results"
SOURCES = REPO / "work" / "_sources"   # local copies of the PDFs (git-ignored); OneDrive frees its own
# Folder runs keep their own work dirs (work/<book>-folder/), so an older local test of the same
# PDF (diacritised, spoken, an older engine) is never reused as raw OCR, and is left untouched.
WORK_VARIANT = "folder"
COST_PER_PAGE = 0.007                  # $ per OCR'd page, measured on the «مؤسسة طيبة» run (247 pages)
# What a book folder in «OCR Results» holds. audio/ is never copied.
COPY_FILES = ["book.json", "ocr_check.html", "reader.html", "timings.jsonl", "usage.jsonl"]
COPY_DIRS = ["pages", "check_pages"]
PAGE_JSON = re.compile(r"^p\d{3,4}\.json$")


# ── finding books ────────────────────────────────────────────────────────────────

def find_books(folder: Path, only: list[str] | None) -> list[dict]:
    found = sorted(bm.scan_publisher(folder).values(), key=lambda b: b["title"])
    books = []
    for b in found:
        if only and not any(bm.norm(o) in b["aliases"] or bm.norm(o) == bm.norm(b["title"]) for o in only):
            continue
        # the copy inside the book's own folder comes first in "pdfs"
        books.append({"title": b["title"], "pdf": folder / b["pdfs"][0], "rel": b["pdfs"][0],
                      "aliases": b["aliases"], "mb": round(b["bytes"] / 1e6, 1)})
    if only:
        for o in only:
            if not any(bm.norm(o) in b["aliases"] or bm.norm(o) == bm.norm(b["title"]) for b in books):
                print(f"! no book matching {o!r} in {folder}")
    return books


def delivered_names() -> tuple[Path | None, dict[str, str]]:
    """The synced «OCR Results» folder and its book folders, by spelling-insensitive title."""
    root = bm.find_cloud(bm.RESULTS_NAME)
    if not root:
        return None, {}
    return root, {bm.norm(d.name): rel for rel, d in bm.result_dirs(root)}


def match(book: dict, names: dict[str, str]) -> str | None:
    return next((names[a] for a in book["aliases"] | {bm.norm(book["title"])} if a in names), None)


# ── running one book ─────────────────────────────────────────────────────────────

def page_count(pdf: Path) -> int:
    with pymupdf.open(pdf) as doc:
        return len(doc)


def page_limit(pages: str) -> int:
    """How many pages a --pages value covers at most ('2' -> 2, '13:20' -> 8, '13:' -> large)."""
    if ":" in pages:
        a, b = pages.split(":")
        return (int(b) - int(a) + 1) if b else 10**6
    return int(pages) or 10**6


def local_copy(folder: Path, rel: str) -> Path:
    """Copy one PDF out of the OneDrive folder once and work from the copy.

    The shared folders are online-only, and OneDrive frees a downloaded file again within
    seconds, sometimes while it is still being read. A local copy is read reliably.
    """
    src, dst = folder / rel, SOURCES / folder.name / rel
    size = src.stat().st_size
    if dst.exists() and dst.stat().st_size == size:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | str = "size mismatch after download"
    for attempt in range(1, 4):
        try:
            shutil.copyfile(src, dst)  # makes OneDrive download it
            if dst.stat().st_size == size:
                page_count(dst)        # opens cleanly
                return dst
        except Exception as e:  # noqa: BLE001  a half-downloaded file: try again
            last = e
        time.sleep(5 * attempt)
    raise RuntimeError(f"could not download {rel} from OneDrive ({last})")


def prepare(books: list[dict], folder: Path) -> None:
    """Copy each book's PDF locally, count its pages, and mark 1-2 page PDFs as covers."""
    counts = bm.load_page_counts()
    for i, b in enumerate(books, 1):
        print(f"\r  preparing {i}/{len(books)}: {b['title']}" + " " * 20, end="", flush=True)
        try:
            b["pdf"] = local_copy(folder, b["rel"])
            b["pages"] = page_count(b["pdf"])
        except Exception as e:  # noqa: BLE001
            b["pages"], b["error"] = None, str(e)
            continue
        counts[f"{folder.name}/{b['rel']}"] = {"pages": b["pages"], "bytes": (folder / b["rel"]).stat().st_size}
    print()
    bm.PAGE_COUNTS.parent.mkdir(parents=True, exist_ok=True)
    bm.PAGE_COUNTS.write_text(json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def run_book(book: dict, *, full: bool, pages: str, log) -> Path:
    """Run the pipeline for one book; returns its work dir. Raises on failure."""
    from pipeline import books as work_index, main as pipeline

    work_dir = work_index.resolve_work_dir(book["pdf"], variant=WORK_VARIANT)
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        if full:
            pipeline.run_pipeline(book["pdf"], ocr="gemini", tts=None, pages=pages, work_dir=work_dir)
            import build_ocr_check
            if any(work_dir.glob("p*.json")):
                build_ocr_check.build(work_dir, book["pdf"])
        else:
            pipeline.run_ocr_check(book["pdf"], pages=pages, work_dir=work_dir, open_page=False)
    return work_dir


def export(book: dict, work_dir: Path, dest: Path, source_label: str) -> None:
    """Copy one book's work into dest/ in the «OCR Results» layout. No local paths leave."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted(work_dir.iterdir()):
        if p.is_file() and PAGE_JSON.match(p.name):
            shutil.copy2(p, dest / p.name)
    for name in COPY_FILES:
        if (work_dir / name).exists():
            shutil.copy2(work_dir / name, dest / name)
    for name in COPY_DIRS:
        if (work_dir / name).is_dir():
            shutil.copytree(work_dir / name, dest / name, dirs_exist_ok=True)
    # book.json records local source paths (/Users/...): keep only "<folder>/<file>"
    meta_path = dest / "book.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update({"title": book["title"], "sources": [f"{source_label}/{book['rel']}"]})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def summarize(dest: Path, total_pages: int | None) -> dict:
    done = sum(1 for p in dest.iterdir() if PAGE_JSON.match(p.name)) if dest.is_dir() else 0
    timings = bm.read_jsonl(dest / "timings.jsonl")
    seconds = sum(t.get("seconds") or 0 for t in timings if not str(t.get("step", "")).endswith(")"))
    prices = bm.PriceTable.from_toml(bm.PRICES.read_text(encoding="utf-8"))
    usage = bm.read_log(dest)
    usd = bm.summarize(usage, prices, by=())[()].usd if usage else 0.0
    check = (dest / "ocr_check.html").exists()
    complete = bool(total_pages) and done >= total_pages and check
    return {"done": done, "total": total_pages, "check": check, "seconds": seconds, "usd": usd,
            "complete": complete}


# ── upload ───────────────────────────────────────────────────────────────────────

def upload(dest: Path, results_root: Path, publisher: str, existing: str | None, overwrite: bool) -> str:
    """Copy a finished book into «OCR Results»/<publisher>/<book>/ (the layout used for every
    publisher since 2026-11-22). A book already there keeps its folder (existing)."""
    if existing:
        target = results_root / existing
    else:
        pub = bm.nfc(publisher)
        # reuse the publisher folder whatever its Unicode form (macOS hands out decomposed names)
        parent = next((d for d in results_root.iterdir() if d.is_dir() and bm.nfc(d.name) == pub),
                      results_root / pub)
        target = parent / bm.nfc(dest.name)
    shown = target.relative_to(results_root)
    if target.exists() and not overwrite:
        return f"skipped: «{shown}» is already in OCR Results (add --overwrite to replace)"
    shutil.copytree(dest, target, dirs_exist_ok=True)
    return f"copied to OCR Results/{shown} (OneDrive uploads it)"


# ── report ───────────────────────────────────────────────────────────────────────

# ── Batch API (half price, results later) ───────────────────────────────────────

BATCHES = REPO / "work" / "_batches"   # one manifest per submitted job, per folder
DONE_STATUSES = ("extracted", "diacritised", "spoken")


def _gemini_client():
    from dotenv import load_dotenv
    from google import genai
    load_dotenv(REPO / ".env")
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _manifests(folder: Path, open_only: bool = True) -> list[Path]:
    d = BATCHES / folder.name
    found = sorted(d.glob("*.json")) if d.is_dir() else []
    return [m for m in found if not open_only or not json.loads(m.read_text(encoding="utf-8")).get("collected")]


def batch_submit(a, folder: Path, todo: list[dict]) -> None:
    """Render every missing page of the books to run and submit them as ONE batch job."""
    from pipeline import books as work_index, ocr_gemini_batch as gb
    from pipeline.ocr_gemini import render_page
    from pipeline.pages import parse_pages

    open_jobs = _manifests(folder)
    if open_jobs:
        print(f"\n«{folder.name}» already has {len(open_jobs)} job(s) waiting. Nothing new was submitted;"
              f" collect them first:\n  python tools/run_folder.py \"{folder}\" --collect")
        return
    start, end = parse_pages(a.pages)
    lines, entries = [], []
    for bi, b in enumerate(todo):
        work_dir = work_index.resolve_work_dir(b["pdf"], variant=WORK_VARIANT)
        (work_dir / "pages").mkdir(parents=True, exist_ok=True)
        sent = []
        with pymupdf.open(b["pdf"]) as doc:
            for i, page in enumerate(doc, start=1):
                if i < start or (end and i > end):
                    continue
                rec = work_dir / f"p{i:03d}.json"
                if rec.exists() and json.loads(rec.read_text(encoding="utf-8")).get("status") in DONE_STATUSES:
                    continue  # already OCR'd: never pay twice
                jpeg, size = render_page(page)
                (work_dir / "pages" / f"p{i:03d}.jpg").write_bytes(jpeg)
                lines.append(gb.request_line(f"{bi}:{i}", jpeg))
                sent.append([i, list(size)])
        entries.append({"title": b["title"], "rel": b["rel"], "pdf": str(b["pdf"]),
                        "work_dir": str(work_dir), "total_pages": b.get("pages"), "pages": sent})
        print(f"  {b['title']}: {len(sent)} page(s) to send")
    if not lines:
        print("\nNo pages need OCR. Nothing was submitted.")
        return
    print(f"\nSubmitting {len(lines)} pages from {len(todo)} book(s) as one batch job ...", flush=True)
    job = gb.submit(_gemini_client(), lines, f"ocr {folder.name}")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    BATCHES.joinpath(folder.name).mkdir(parents=True, exist_ok=True)
    (BATCHES / folder.name / f"{stamp}.json").write_text(json.dumps({
        "job": job.name, "folder": folder.name, "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "requests": len(lines), "pages_arg": a.pages, "books": entries, "collected": False,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Submitted: {job.name}  ({gb.state(job)})\n"
          f"Google targets 24 h, usually much sooner; a job not finished in 48 h expires.\n"
          f"Collect with:\n  python tools/run_folder.py \"{folder}\" --collect")


def batch_collect(a, folder: Path, source_label: str) -> None:
    """Fetch finished jobs, write the page records, build review pages, export like a normal run."""
    import hashlib
    from pipeline import ocr_gemini_batch as gb, usage
    from pipeline.run_ocr_gemini import finish_page
    import build_ocr_check

    jobs = _manifests(folder)
    if not jobs:
        print(f"No open batch jobs for «{folder.name}».")
        return
    client = _gemini_client()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for mpath in jobs:
        man = json.loads(mpath.read_text(encoding="utf-8"))
        job = client.batches.get(name=man["job"])
        st = gb.state(job)
        waited = time.time() - time.mktime(time.strptime(man["submitted"], "%Y-%m-%dT%H:%M:%S"))
        stats = getattr(job, "completion_stats", None)
        print(f"\n{man['job']}: {st} · {man['requests']} pages · submitted {fmt_time(waited)} ago"
              + (f" · {stats}" if stats else ""))
        if st not in gb.FINISHED:
            print("  not finished yet: run --collect again later.")
            continue
        books = man["books"]
        got = {}  # book index -> [ok, failed]
        if st in gb.HAS_RESULTS:
            hashes = {}
            for key, lines, units, error in gb.results(client, job):
                bi, pg = (int(x) for x in key.split(":"))
                e = books[bi]
                work_dir, pdf = Path(e["work_dir"]), Path(e["pdf"])
                size = next(sz for p_, sz in e["pages"] if p_ == pg)
                with usage.recording(work_dir, "ocr-gemini-batch"), usage.page(pg):
                    if units:
                        usage.record("gemini", gb.PRICE_MODEL, "ocr", images=1, **units)
                c = got.setdefault(bi, [0, 0])
                if lines is None:
                    c[1] += 1
                    print(f"  ! {e['title']} p{pg}: {error}")
                    continue
                if pdf not in hashes:
                    hashes[pdf] = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
                finish_page(lines, pg, pdf.stem, pdf, hashes[pdf], work_dir, size)
                c[0] += 1
        else:
            print("  the job returned nothing; its pages will be sent again by the next --batch.")
        for bi, e in enumerate(books):
            work_dir = Path(e["work_dir"])
            dest = a.out / e["title"]
            ok, bad = got.get(bi, [0, 0])
            if any(work_dir.glob("p[0-9][0-9][0-9].json")):
                with contextlib.redirect_stdout(open(os.devnull, "w")):
                    build_ocr_check.build(work_dir, Path(e["pdf"]))
                export({"title": e["title"], "rel": e["rel"]}, work_dir, dest, source_label)
            s = summarize(dest, e.get("total_pages")) if dest.is_dir() else {"done": 0, "check": False, "usd": 0, "complete": False}
            rows.append([e["title"], f"{ok} ok" + (f", {bad} failed" if bad else ""),
                         f"{s['done']}/{e.get('total_pages') or '?'}", "yes" if s["check"] else "no",
                         f"${s['usd']:.4f}", "complete" if s["complete"] else "partial"])
        man.update({"collected": True, "state": st, "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "turnaround_s": round(waited)})
        mpath.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    if rows:
        print()
        table(rows, ["book", "this job", "pages on disk", "check page", "cost so far", "status"])
        print(f"\nResults: {a.out}. Upload the complete books with --upload.")


def fmt_time(s: float) -> str:
    return "—" if not s else (f"{s / 60:.0f} min" if s >= 90 else f"{s:.0f} s")


def table(rows: list[list[str]], head: list[str]) -> None:
    width = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    print("  ".join(h.ljust(w) for h, w in zip(head, width)))
    print("  ".join("-" * w for w in width))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, width)))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="folder of books (e.g. a publisher folder)")
    ap.add_argument("--book", action="append", help="only this book (title, any spelling); repeatable")
    ap.add_argument("--dry-run", action="store_true", help="list books and what would run; no API calls")
    ap.add_argument("--full", action="store_true", help="also diacritics + reader.html (never audio)")
    ap.add_argument("--pages", default="0", help="'0' all pages (default), '2' pages 1-2, '13:20'")
    ap.add_argument("--redo", action="store_true",
                    help="also run books that are already in «OCR Results» (default: skip them)")
    ap.add_argument("--out", type=Path, default=OUT, help=f"local results folder (default: {OUT.relative_to(REPO)})")
    ap.add_argument("--upload", action="store_true",
                    help="copy finished books into the synced «OCR Results» folder (OneDrive uploads)")
    ap.add_argument("--overwrite", action="store_true", help="with --upload: replace a book already there")
    ap.add_argument("--batch", action="store_true",
                    help="submit the missing pages as one Gemini Batch job (half price, results later)")
    ap.add_argument("--collect", action="store_true",
                    help="fetch finished batch jobs for this folder, build review pages and export")
    a = ap.parse_args()

    folder = a.folder.expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")
    os.chdir(REPO)  # the pipeline keeps work/ relative to the repo
    books = find_books(folder, a.book)
    if not books:
        sys.exit(f"no books found in {folder}")
    results_root, delivered = delivered_names()
    source_label = folder.name

    print(f"{len(books)} books in «{folder.name}»"
          + ("" if results_root else "  (OCR Results not synced: can't tell which are delivered)"))
    for b in books:
        b["already"] = match(b, delivered)
    pending = [b for b in books if a.redo or not b["already"]]
    if pending:
        print(f"Copying {len(pending)} PDF(s) locally and counting pages (free; downloads from OneDrive):")
        prepare(pending, folder)

    plan = []
    for b in books:
        local = a.out / b["title"]
        state = summarize(local, None) if local.is_dir() else None
        pages = b.get("pages")
        if b["already"] and not a.redo:
            b["action"] = "skip: already in OCR Results"
        elif b.get("error"):
            b["action"] = "skip: could not download (run again)"
        elif pages is not None and pages < bm.MIN_BOOK_PAGES:
            b["action"] = f"skip: cover ({pages} page{'s' if pages > 1 else ''})"
        elif pages and local.is_dir() and summarize(local, pages)["complete"]:
            b["action"] = "skip: done locally (upload with --upload)"
        else:
            b["action"] = "resume" if state and state["done"] else "run"
        est = (pages if a.pages == "0" else min(pages or 0, page_limit(a.pages))) if pages else None
        b["estimate"] = est * COST_PER_PAGE if est and not b["action"].startswith("skip") else 0
        plan.append([b["title"], f"{b['mb']} MB", pages if pages is not None else "—",
                     f"${b['estimate']:.2f}" if b["estimate"] else "—", b["already"] or "—",
                     f"{state['done']} pages" if state else "—", b["action"]])
    table(plan, ["book", "PDF", "pages", "est. cost", "in OCR Results", "local result", "plan"])

    todo = [b for b in books if not b["action"].startswith("skip")]
    est_pages = sum((b.get("pages") or 0) for b in todo) if a.pages == "0" else None
    print(f"\n{len(todo)} to run" + (f", {est_pages} pages" if est_pages else "")
          + f", about ${sum(b['estimate'] for b in todo):.2f} and "
          f"{sum(min(b.get('pages') or 0, page_limit(a.pages)) for b in todo) * 14 / 60:.0f} min "
          f"at ~${COST_PER_PAGE} and ~14 s a page.")
    if a.batch:
        print(f"With --batch: about ${sum(b['estimate'] for b in todo) / 2:.2f} (half price), "
              "results within 24 h instead of now.")
    if a.dry_run:
        print("Nothing was run (--dry-run).")
        return
    if a.collect:
        batch_collect(a, folder, source_label)
        return
    if a.batch:
        if a.full:
            sys.exit("--batch does the paid OCR only; run --full afterwards without --batch.")
        batch_submit(a, folder, todo)
        return
    if not todo and not a.upload:
        print("\nNothing to run.")
        return

    a.out.mkdir(parents=True, exist_ok=True)
    log_path = a.out / "run_folder.log"
    results = []
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}  {folder} =====\n")
        for i, b in enumerate(todo, 1):
            dest = a.out / b["title"]
            print(f"\n[{i}/{len(todo)}] {b['title']} ...", flush=True)
            started = time.perf_counter()
            try:
                total = b["pages"]
                work_dir = run_book(b, full=a.full, pages=a.pages, log=log)
                export(b, work_dir, dest, source_label)
                s = summarize(dest, total)
                note = "" if s["complete"] else ("partial: run again to finish" if a.pages == "0"
                                                 else f"--pages {a.pages}")
            except KeyboardInterrupt:
                print("  stopped (Ctrl-C). Run the same command again to resume.")
                break
            except Exception as e:  # one book failing must not stop the folder
                log.write(f"\n!! {b['title']}: {type(e).__name__}: {e}\n")
                s = summarize(dest, None) if dest.is_dir() else {"done": 0, "total": None, "check": False,
                                                                  "seconds": 0, "usd": 0, "complete": False}
                note = f"failed: {type(e).__name__}: {str(e)[:80]} (details in the log)"
            b["summary"], b["note"] = s, note
            print(f"  {s['done']}/{s['total'] or '?'} pages · check page {'yes' if s['check'] else 'no'} · "
                  f"{fmt_time(time.perf_counter() - started)} · ${s['usd']:.2f}" + (f" · {note}" if note else ""))
            results.append(b)

    # upload: complete books only, and never over an existing book folder without --overwrite
    if a.upload:
        if not results_root:
            print("\n! OCR Results isn't synced on this Mac: nothing uploaded.")
        else:
            ready = [b for b in books if (a.out / b["title"]).is_dir() and b.get("pages")
                     and summarize(a.out / b["title"], b["pages"])["complete"]]
            print(f"\nUploading {len(ready)} complete book(s) to «{results_root.name}»:")
            for b in ready:
                b["upload"] = upload(a.out / b["title"], results_root, folder.name, b.get("already"), a.overwrite)
                print(f"  {b['title']}: {b['upload']}")
            for b in books:
                if (a.out / b["title"]).is_dir() and b not in ready:
                    print(f"  {b['title']}: not uploaded, not complete yet")

    if results:
        print()
        table([[b["title"], f"{b['summary']['done']}/{b['summary']['total'] or '?'}",
                "yes" if b["summary"]["check"] else "no", fmt_time(b["summary"]["seconds"]),
                f"${b['summary']['usd']:.2f}", b.get("upload", "—" if not a.upload else "not uploaded"),
                b["note"] or "done"] for b in results],
              ["book", "pages", "check page", "OCR time", "cost", "upload", "status"])
        print(f"\nResults: {a.out.relative_to(REPO) if a.out.is_relative_to(REPO) else a.out}   "
              f"Log: {log_path.name}")
        if not a.upload:
            print("Add --upload to copy the complete books into «OCR Results».")


if __name__ == "__main__":
    main()
