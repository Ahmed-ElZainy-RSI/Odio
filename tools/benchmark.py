"""Benchmark: every Reading First book, where it stands, and what its OCR took.

Reads the two shared SharePoint folders through the OneDrive app (online-only shortcuts):

  "Reading First Book Sample"  publisher/ -> book PDFs     the books we must do
  "OCR Results"                book/      -> OCR output    what has been delivered to managers

Nothing large is downloaded. Book lists, page counts and delivery dates come from file names,
sizes and dates, which macOS reports for cloud placeholders without fetching them. Only the
small run logs delivered with each result (timings.jsonl, usage.jsonl, book.json; ~300 KB in
all) are read, which makes OneDrive fetch those files. PDFs and images are never opened.

Audio (speech + word alignment) is not delivered to SharePoint yet; it comes from the local
pipeline runs in work/<book>/ and is labelled "local run".

A source "book" is one interior PDF. Cover files (names with "cover" / "غلاف") are never books,
and a loose PDF that duplicates a book folder in the same publisher counts once. Titles are matched
across the two folders after normalising spelling (hamza forms, ة/ه, ى/ي, diacritics, and words
such as "داخلي" or "بعد التعديل").

Usage:
    python3 tools/benchmark.py                              # writes result/benchmark.json
    python3 tools/benchmark.py --dashboard DIR              # also books.json + step_times.json
    python3 tools/benchmark.py --source DIR --results DIR   # folders elsewhere
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from urllib.parse import quote
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from pipeline.usage import read_log  # noqa: E402
from pipeline.usage_model import PriceTable, summarize  # noqa: E402

PRICES = REPO_ROOT / "config" / "prices.toml"
# A PDF with fewer pages than this is a cover, not a book. Counting pages means opening the PDF,
# which the benchmark never does: tools/run_folder.py counts them when it downloads a folder and
# saves them here, keyed "<publisher>/<path in publisher folder>", with the file's size.
MIN_BOOK_PAGES = 3
PAGE_COUNTS = REPO_ROOT / "result" / "page_counts.json"


def load_page_counts() -> dict:
    try:
        return json.loads(PAGE_COUNTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def known_pages(counts: dict, publisher: str, rel: str, size: int) -> int | None:
    """Saved page count for this file, if it was counted and hasn't changed size since."""
    c = counts.get(f"{publisher}/{rel}")
    return c["pages"] if c and c.get("bytes") == size else None
CLOUD = Path.home() / "Library" / "CloudStorage"
SOURCE_NAME = "Reading First Book Sample"
RESULTS_NAME = "OCR Results"
# Where the two shared folders live on SharePoint, so each book can link to its source PDF and
# its result folder. Links open only for people who have access to those folders.
SHAREPOINT = "https://srsdxb-my.sharepoint.com"
SOURCE_WEB = ("/personal/moh_roshdey_rsi-tech_com", "/personal/moh_roshdey_rsi-tech_com/Documents/Reading First Book Sample")
RESULTS_WEB = ("/personal/w_ashraf_rsi-tech_com", "/personal/w_ashraf_rsi-tech_com/Documents/OCR Results")
WORK = REPO_ROOT / "work"
PAGE_JSON = re.compile(r"^p(\d{3,4})\.json$")
PAGE_IMG = re.compile(r"^p(\d{3,4})\.(jpe?g|png)$", re.I)
NOISE_WORDS = ["المشروع الوطني للقراءة", "داخلي", "بعد التعديل", "للطباعة", "كاملة", "pdf", "copy", "نسخة", "merged",
               "cover", "غلاف"]


# ── names ──────────────────────────────────────────────────────────────────────

def _chars(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub("[ً-ٰٟـ]", "", s)              # harakat, dagger alef, tatweel
    s = re.sub("[أإآٱ]", "ا", s)          # hamza/madda alef forms -> alef
    for a, b in (("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)                                       # alef maqsura, taa marbuta, hamza seats
    return re.sub(r"[؟?!،,.\-_()\[\]«»\"']", " ", s).lower()


def norm(name: str) -> str:
    """Spelling-insensitive key for matching a title across folders."""
    s = _chars(re.sub(r"\s*pdf$", "", re.sub(r"\.(pdf|tif|jpe?g)$", "", name, flags=re.I), flags=re.I))
    s = re.sub(r"^\s*(cover|غلاف)\s+2\s+", "", s)               # "cover 2 X" is the back cover of X
    for w in NOISE:
        s = re.sub(rf"(^|\s){re.escape(w)}(?=\s|$)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


NOISE = [_chars(w).strip() for w in NOISE_WORDS]


def display(name: str) -> str:
    s = re.sub(r"\.(pdf)$", "", name, flags=re.I)
    s = re.sub(r"^\s*(داخلي|cover\s*2?|غلاف)\s+", "", s, flags=re.I)
    s = re.sub(r"_merged$", "", s)
    s = re.sub(r"\s*-\s*copy(\s*\(\d+\))?$", "", s, flags=re.I)
    s = re.sub(r"\s*(final|\(\d\)|-\d)$", "", re.sub(r"pdf$", "", s, flags=re.I), flags=re.I).replace("_", " ")
    s = re.sub(r"\s+(pdf|بعد التعديل|للطباعة|المشروع الوطني للقراءة)\s*$", "", s, flags=re.I)
    return nfc(re.sub(r"\s+", " ", s).strip())


def is_cover(name: str) -> bool:
    return "cover" in name.lower() or "غلاف" in name


def find_cloud(name: str) -> Path | None:
    """The synced shortcut. OneDrive names it "<Owner>'s files - <folder>"."""
    if not CLOUD.is_dir():
        return None
    for root in sorted(CLOUD.iterdir()):
        if not root.is_dir():
            continue
        for hit in sorted(root.iterdir()):
            if hit.is_dir() and (hit.name == name or hit.name.endswith(f" - {name}")):
                return hit
    return None


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# ── source folder: publisher / (book.pdf | book folder | series folder / book folder) ──

def interiors(folder: Path, filenames: list[str], is_book_folder: bool) -> tuple[list[str], list[str]]:
    """Split a folder's PDFs into (interiors, covers).

    Named covers ("cover", "غلاف") are covers everywhere. Inside a book folder, two more signs
    mark a cover: a "داخلي" (interior) file sits beside it, or it is under 15% of the size of
    the folder's largest PDF (e.g. "jelly fish new.pdf", 1.4 MB, next to a 25 MB interior).
    """
    pdfs = sorted(f for f in filenames if f.lower().endswith(".pdf"))
    covers = [f for f in pdfs if is_cover(f)]
    rest = [f for f in pdfs if f not in covers]
    if is_book_folder and len(rest) > 1:
        marked = [f for f in rest if f.strip().startswith("داخلي")]
        if marked:
            covers += [f for f in rest if f not in marked]
            rest = marked
        else:
            size = {f: (folder / f).stat().st_size for f in rest}
            big = max(size.values())
            covers += [f for f in rest if size[f] < 0.15 * big]
            rest = [f for f in rest if size[f] >= 0.15 * big]
    return rest, covers


def scan_source(root: Path) -> dict[tuple, dict]:
    """(publisher, title key) -> book, for every publisher folder under root."""
    books: dict[tuple, dict] = {}
    for pub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        books.update(scan_publisher(pub))
    return books


def scan_publisher(pub: Path) -> dict[tuple, dict]:
    """(publisher, title key) -> book for one folder of books (a publisher, or any batch folder).

    Only interior PDFs make books; covers are never books. Each book's "pdfs" are paths relative
    to the folder, the copy inside a book folder first.
    """
    books: dict[tuple, dict] = {}
    alias: dict[str, tuple] = {}                     # any spelling seen -> its book key
    cover_names: set[str] = set()                    # file names judged covers inside book folders
    # Bottom-up: book folders first, so a loose copy at the publisher level of a file already
    # judged a cover is dropped, and a loose copy of an interior merges into its book.
    for dirpath, dirnames, filenames in sorted(os.walk(pub, topdown=False), key=lambda w: -w[0].count(os.sep)):
        folder = Path(dirpath)
        pdfs, covers = interiors(folder, filenames, folder != pub)
        cover_names.update(c.strip().lower() for c in covers)
        if folder == pub:
            pdfs = [f for f in pdfs if f.strip().lower() not in cover_names]
        for f in pdfs:
            # A book folder holding one interior PDF is named for the book; prefer that name.
            title = folder.name if len(pdfs) == 1 and folder != pub else f
            k, alt = norm(title), norm(f)
            key = alias.get(k) or alias.get(alt) or (pub.name, k)
            st = (folder / f).stat()
            b = books.setdefault(key, {"title": display(title), "publisher": pub.name, "files": 0,
                                       "bytes": 0, "added": iso(st.st_mtime), "aliases": set(),
                                       "pdfs": []})
            b["files"] += 1
            b["pdfs"].append(str((folder / f).relative_to(pub)))
            b["bytes"] = max(b["bytes"], st.st_size)
            b["aliases"].update({k, alt})
            b["added"] = min(b["added"], iso(st.st_mtime))
            alias[k] = alias[alt] = key
    # Drop "books" whose every PDF is known to have 1-2 pages: those are covers.
    counts = load_page_counts()
    for key in [k for k, b in books.items() if b["pdfs"]]:
        pages = [known_pages(counts, pub.name, rel, (pub / rel).stat().st_size) for rel in books[key]["pdfs"]]
        if all(n is not None and n < MIN_BOOK_PAGES for n in pages):
            del books[key]
    return books


# ── audio: made only by the local pipeline (work/<book>/), never delivered to SharePoint yet ──

def pdf_pages(path: Path) -> int | None:
    """Page count of a local PDF from Spotlight (no parsing)."""
    import subprocess
    try:
        out = subprocess.run(["mdls", "-raw", "-name", "kMDItemNumberOfPages", str(path)],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out) if out.isdigit() else None
    except (OSError, subprocess.SubprocessError):
        return None


def scan_audio(prices: PriceTable) -> dict[str, dict]:
    """norm(title) -> audio made by local pipeline runs (main variant of each book in work/)."""
    index = WORK / "index.json"
    if not index.exists():
        return {}
    out = {}
    for book in json.loads(index.read_text(encoding="utf-8")).get("books", {}).values():
        d = WORK / book["dirs"].get("", "")
        if not book["dirs"].get("") or not d.is_dir():
            continue
        recs = [json.loads(x.read_text(encoding="utf-8")) for x in sorted(d.glob("p[0-9][0-9][0-9].json"))]
        spoken = [r for r in recs if r.get("status") == "spoken" and r.get("audio")]
        if not spoken:
            continue
        total = next((pdf_pages(REPO_ROOT / s) for s in book.get("sources", []) if (REPO_ROOT / s).exists()), None)
        timings = [t for t in read_jsonl(d / "timings.jsonl") if t.get("step") in ("tts", "align")]
        usage = [u for u in read_log(d) if u.operation == "tts" or (u.stage or "").startswith("tts")]
        usd = summarize(usage, prices, by=())[()].usd if usage else None
        out[norm(book["title"])] = {
            "audio_pages": len(spoken), "audio_of": total or len(recs),
            "audio_minutes": round(sum(r.get("duration_ms") or 0 for r in spoken) / 60000, 1),
            "audio_voice": next((r.get("voice") for r in spoken if r.get("voice")), None),
            "audio_seconds": round(sum(t["seconds"] for t in timings), 1) if timings else None,
            "audio_usd": round(usd, 4) if usd is not None else None,
        }
    return out


# ── results folder: one folder per delivered book ─────────────────────────────

def read_text(path: Path, tries: int = 4) -> str:
    """Read a small file from the OneDrive folder. Opening an online-only file makes OneDrive
    download it, which can time out (Errno 60) while it is busy uploading: wait and retry."""
    import time
    for attempt in range(1, tries + 1):
        try:
            return path.read_text(encoding="utf-8")
        except (TimeoutError, OSError) as e:
            if attempt == tries:
                raise RuntimeError(f"could not read {path.parent.name}/{path.name} from OneDrive: {e}") from e
            time.sleep(10 * attempt)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in read_text(path).splitlines() if l.strip()]


def is_book_dir(d: Path) -> bool:
    return (d / "book.json").exists() or any(PAGE_JSON.match(n) for n in os.listdir(d))


def result_dirs(results: Path) -> list[tuple[str, Path]]:
    """Every book folder in «OCR Results»: "<book>/" at the top, or "<publisher>/<book>/" one level
    down (the layout used since the «مؤسسة طيبة» upload). Returns (path relative to results, dir)."""
    found = []
    for d in sorted(results.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if is_book_dir(d):
            found.append((d.name, d))
            continue
        for sub in sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith(".")):
            if is_book_dir(sub):
                found.append((f"{d.name}/{sub.name}", sub))
    return found


def scan_result(d: Path, prices: PriceTable) -> dict:
    names = [n for n in os.listdir(d) if not n.startswith(".")]  # skip .DS_Store and other hidden files
    ocr_pages = sorted(int(m.group(1)) for n in names if (m := PAGE_JSON.match(n)))
    page_dir = d / "pages"
    rendered = sorted(int(m.group(1)) for n in (os.listdir(page_dir) if page_dir.is_dir() else [])
                      if (m := PAGE_IMG.match(n)))
    total = max(rendered + ocr_pages) if (rendered or ocr_pages) else 0
    # File dates are SharePoint's modified times; folder dates are when OneDrive made them locally.
    mtimes = [os.stat(d / n).st_mtime for n in names if (d / n).is_file()]

    timings = read_jsonl(d / "timings.jsonl")
    steps = [t for t in timings if not str(t.get("step", "")).endswith("(total)")]
    seconds = defaultdict(float)
    for t in steps:
        seconds[re.sub(r"\s*\(estimated\)$", "", t["step"])] += t.get("seconds") or 0
    estimated = any("estimated" in str(t.get("step", "")) + str(t.get("stage", "")) for t in timings)
    failed = sum(1 for t in steps if t.get("ok") is False)
    ats = sorted(t["at"] for t in timings if t.get("at"))

    read_text(d / "usage.jsonl") if (d / "usage.jsonl").exists() else None  # fetch from OneDrive, with retries
    usage = read_log(d)
    usd = unpriced = 0.0
    if usage:
        line = summarize(usage, prices, by=())[()]
        usd, unpriced = line.usd, line.unpriced
    tokens_in = sum(u.input_tokens for u in usage)
    tokens_out = sum(u.output_tokens for u in usage)

    # Arabic review page (ocr_check.html): it holds one image per OCR'd page that has words
    # (tools/build_ocr_check.py skips textless pages). A page record under ~1 KB has no words.
    check = d / "ocr_check.html"
    check_dir = d / "check_pages"
    checked = {int(m.group(1)) for n in (os.listdir(check_dir) if check_dir.is_dir() else [])
               if (m := PAGE_IMG.match(n))}
    text_pages = {pg for pg in ocr_pages if (d / f"p{pg:03d}.json").stat().st_size >= 1000}
    unreviewable = sorted(text_pages - checked)
    stale = sorted(checked - set(ocr_pages))
    if not check.exists():
        review = "missing"
    elif stale:
        review = "rebuild"
    elif unreviewable:
        review = "gaps"
    else:
        review = "ready"

    has_book = (d / "book.json").exists()
    if has_book:
        meta = json.loads(read_text(d / "book.json"))
    else:
        meta = {}
    done = len(ocr_pages)
    state = "done" if total and done >= total and has_book else ("partial" if done else "pending")
    return {
        "folder": d.name, "title": meta.get("title") or d.name,
        "pages_total": total, "pages_ocr": done,
        "missing_pages": [p for p in range(1, total + 1) if p not in set(ocr_pages)][:50],
        "ocr_state": state, "has_book_json": has_book,
        "review_page": "ocr_check.html" in names, "reader_page": "reader.html" in names,
        "review": review, "review_missing": unreviewable, "review_stale": stale,
        "review_mb": round(check.stat().st_size / 1e6, 1) if check.exists() else None,
        "review_updated": iso(check.stat().st_mtime) if check.exists() else None,
        "delivered_at": iso(max(mtimes)) if mtimes else None,
        "first_upload": iso(min(mtimes)) if mtimes else None,
        "started_at": ats[0] if ats else None, "finished_at": ats[-1] if ats else None,
        "runs": len({t.get("run") for t in timings if t.get("run")}),
        "seconds": {k: round(v, 2) for k, v in sorted(seconds.items())},
        "seconds_total": round(sum(seconds.values()), 2) if seconds else None,
        "timing_estimated": estimated, "failed_calls": failed,
        "calls": len(usage), "tokens_in": tokens_in, "tokens_out": tokens_out,
        "usd": round(usd, 5) if usage else None, "unpriced_calls": int(unpriced),
    }


# ── join ───────────────────────────────────────────────────────────────────────

def build(source: Path, results: Path) -> dict:
    prices = PriceTable.from_toml(PRICES.read_text(encoding="utf-8"))
    src = scan_source(source)
    res = {}
    for rel, d in result_dirs(results):
        r = scan_result(d, prices)
        r["folder"] = rel                       # "<book>" or "<publisher>/<book>"
        res[norm(d.name)] = r
    alias = {a: k for k, b in src.items() for a in b["aliases"]}
    audio = scan_audio(prices)

    books, matched = [], set()
    for k, b in src.items():
        r = res.get(k) or next((res[a] for a in b["aliases"] if a in res), None)
        if r:
            matched.add(norm(r["folder"]))
        au = next((audio[a] for a in b["aliases"] if a in audio), None)
        books.append(row(b, r, au))
    for k, r in res.items():
        if k not in matched and k not in alias:
            books.append(row(None, r, audio.get(k)))
    books.sort(key=lambda x: (x["publisher"] or "~", x["book"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices_as_of": prices.as_of,
        "source_folder": source.name, "results_folder": results.name,
        "books": books,
    }


def nfc(s: str) -> str:
    """SharePoint's spelling of a name. macOS (and the OneDrive folder on it) hands names out
    decomposed: «أ» as «ا» + a separate hamza (U+0627 U+0654), likewise «إ» «آ» «ؤ» «ئ».
    SharePoint stores the composed letter (U+0623), so a link built from the decomposed name 404s."""
    return unicodedata.normalize("NFC", s)


def file_link(site_folder: tuple[str, str], *parts: str) -> str:
    """A SharePoint link that opens a file in the browser viewer."""
    path = nfc("/".join([site_folder[1], *parts]))
    return f"{SHAREPOINT}{quote(path)}?web=1"


def folder_link(site_folder: tuple[str, str], *parts: str) -> str:
    """A SharePoint link that opens a folder in the OneDrive web view."""
    site, folder = site_folder
    path = nfc("/".join([folder, *parts]))
    return f"{SHAREPOINT}{site}/_layouts/15/onedrive.aspx?id={quote(path, safe='')}"


def row(s: dict | None, r: dict | None, au: dict | None = None) -> dict:
    if r and r["ocr_state"] == "done":
        status = "delivered"
    elif r:
        status = "in progress"
    else:
        status = "not started"
    secs = r and r["seconds_total"]
    return {
        "book": display(r["folder"].split("/")[-1]) if r else s["title"],
        "publisher": nfc(s["publisher"]) if s else None,
        "in_source": "yes" if s else "no",
        "status": status,
        "result_folder": r and nfc(r["folder"]),
        "pages": r and r["pages_total"] or None,
        "pages_ocr": r and r["pages_ocr"] or 0,
        "ocr": r["ocr_state"] if r else "pending",
        "review_page": "done" if r and r["review_page"] else "pending",
        "review": r["review"] if r else "none",
        "review_missing": ", ".join(map(str, r["review_missing"])) if r and r["review_missing"] else None,
        "review_missing_count": len(r["review_missing"]) if r else 0,
        "review_mb": r and r["review_mb"],
        "reader_page": "done" if r and r["reader_page"] else "pending",
        "added": ((s or {}).get("added") or "")[:10] or None,
        "started": (r and r["started_at"] or "")[:10] or None,
        "delivered": (r and r["delivered_at"] or "")[:10] or None if status == "delivered" else None,
        "seconds": secs,
        "seconds_per_page": round(secs / r["pages_total"], 2) if secs and r["pages_total"] else None,
        "timing": ("estimated" if r["timing_estimated"] else "logged") if r and r["seconds_total"] else "none",
        "runs": r and r["runs"] or 0,
        "failed_calls": r and r["failed_calls"] or 0,
        "usd": r and r["usd"],
        "source_mb": round(s["bytes"] / 1e6, 1) if s else None,
        "source_file": nfc(f"{s['publisher']}/{s['pdfs'][0]}") if s else None,
        "source_url": file_link(SOURCE_WEB, s["publisher"], *s["pdfs"][0].split("/")) if s else None,
        "result_url": folder_link(RESULTS_WEB, r["folder"]) if r else None,
        "audio": ("done" if au["audio_pages"] >= au["audio_of"] else "partial") if au else "none",
        "audio_pages": au["audio_pages"] if au else 0,
        "audio_of": au["audio_of"] if au else None,
        "audio_minutes": au["audio_minutes"] if au else None,
        "audio_voice": au["audio_voice"] if au else None,
        "audio_seconds": au["audio_seconds"] if au else None,
        "audio_usd": au["audio_usd"] if au else None,
        "audio_where": "local run" if au else None,
    }


def export_dashboard(data: dict, results_dir: Path, out_dir: Path) -> None:
    """Flat {rows, meta} files for the managers' dashboard. No local paths leave the machine."""
    meta = [
        {"label": "benchmark.py", "value": Path(__file__).read_text(encoding="utf-8"), "type": "code", "lang": "python"},
        {"label": "Generated", "value": data["generated_at"]},
        {"label": "Book list from", "value": f"SharePoint: {data['source_folder']}"},
        {"label": "Results from", "value": f"SharePoint: {data['results_folder']}"},
        {"label": "Prices as of", "value": data["prices_as_of"]},
    ]
    times = []
    for b in data["books"]:
        if not b["result_folder"]:
            continue
        for t in read_jsonl(results_dir / b["result_folder"] / "timings.jsonl"):
            step = str(t.get("step", ""))
            if step.endswith("(total)"):
                continue
            times.append({"book": b["book"], "step": re.sub(r"\s*\(estimated\)$", "", step),
                          "estimated": "estimated" in step, "ok": t.get("ok", True),
                          "seconds": t.get("seconds") or 0, "page": t.get("page"),
                          "day": (t.get("at") or "")[:10] or None})
    # One row per book and step keeps the file small; pages shown where the log has them.
    agg = defaultdict(lambda: {"seconds": 0.0, "calls": 0, "failed": 0, "pages": set(), "estimated": False})
    for t in times:
        a = agg[(t["book"], t["step"])]
        a["seconds"] += t["seconds"]; a["calls"] += 1; a["failed"] += t["ok"] is False
        a["estimated"] |= t["estimated"]
        if t["page"] is not None:
            a["pages"].add(t["page"])
    step_rows = [{"book": bk, "step": st, "seconds": round(a["seconds"], 2), "calls": a["calls"],
                  "failed": a["failed"], "pages": len(a["pages"]), "estimated": a["estimated"]}
                 for (bk, st), a in sorted(agg.items())]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("books", data["books"]), ("step_times", step_rows)):
        (out_dir / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False),
                                              encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, help=f'source folder (default: synced "{SOURCE_NAME}")')
    ap.add_argument("--results", type=Path, help=f'results folder (default: synced "{RESULTS_NAME}")')
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "result" / "benchmark.json")
    ap.add_argument("--dashboard", type=Path, help="also write books.json + step_times.json here")
    a = ap.parse_args()
    source = a.source or find_cloud(SOURCE_NAME)
    results = a.results or find_cloud(RESULTS_NAME)
    missing = [n for n, f in ((SOURCE_NAME, source), (RESULTS_NAME, results)) if not f]
    if missing:
        sys.exit(f"Not synced yet: {', '.join(missing)}. In the OneDrive app, add each shared folder "
                 f"with 'Add shortcut to My files' and keep it online-only; it then appears under {CLOUD}.")
    data = build(source, results)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.dashboard:
        export_dashboard(data, results, a.dashboard)

    bs = data["books"]
    print(f"{len(bs)} books -> {a.out}")
    print("  status:   ", dict(Counter(b["status"] for b in bs)))
    print("  in source:", dict(Counter(b["in_source"] for b in bs)))
    print("  publishers:", dict(Counter(b["publisher"] for b in bs)))


if __name__ == "__main__":
    main()
