"""Which work directory belongs to a PDF -- decided by its content, not its filename (spec 013).

Book identity used to be the filename stem: rename a PDF and its finished work was
orphaned, copy it under another name and it was processed (and paid for) twice, and two
different PDFs that happened to share a name wrote into the same directory.

Now a book's id is the first 16 hex characters of the PDF's SHA-256 -- the same
`pdf_sha256_16` every page record has always carried, so existing books can be indexed
from their own records (`migrate`). `work/index.json` maps id -> directory per variant
("" for the main run, "lighton" for the second engine). Directory names stay readable
(the title at first sight); only the lookup changed.

Usage:
    python3 src/pipeline/books.py migrate work/      # index existing books (idempotent)
    python3 src/pipeline/books.py show work/         # list what the index knows
"""
import hashlib
import json
import sys
from pathlib import Path

INDEX_NAME = "index.json"
BOOK_FILE = "book.json"
FORMAT = 1


def book_id(pdf_path: Path) -> str:
    """First 16 hex chars of the file's SHA-256 (== pdf_sha256_16 in page records)."""
    h = hashlib.sha256()
    with Path(pdf_path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _load(root: Path) -> dict:
    path = root / INDEX_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"format": FORMAT, "books": {}}


def _save(root: Path, index: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (INDEX_NAME + ".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(root / INDEX_NAME)


def _hashes_in(work_dir: Path) -> set[str]:
    """The pdf_sha256_16 values the page records in a directory were made from."""
    found = set()
    for rec in sorted(work_dir.glob("p*.json")):
        try:
            h = json.loads(rec.read_text(encoding="utf-8")).get("pdf_sha256_16")
        except (ValueError, OSError):
            continue
        if h:
            found.add(h)
    return found


def _dir_name(title: str, variant: str) -> str:
    return f"{title}-{variant}" if variant else title


def resolve_work_dir(pdf_path: Path, *, root: Path = Path("work"), variant: str = "") -> Path:
    """The directory this PDF's work lives in, creating the index entry if needed."""
    pdf_path, root = Path(pdf_path), Path(root)
    bid = book_id(pdf_path)
    title = pdf_path.stem.strip()
    index = _load(root)
    entry = index["books"].setdefault(bid, {"title": title, "dirs": {}, "sources": []})

    known = entry["dirs"].get(variant)
    if known and (root / known).is_dir():
        work_dir = root / known
        if title != entry["title"]:
            print(f"{pdf_path.name}: same content as '{entry['title']}' -- "
                  f"reusing {work_dir} (book {bid})")
    else:
        name = _dir_name(entry["title"], variant)
        hashes = _hashes_in(root / name) if (root / name).is_dir() else set()
        if hashes and bid not in hashes:
            # a different PDF already owns this name: never write into its work
            name = _dir_name(f"{entry['title']}-{bid[:8]}", variant)
            print(f"{root / _dir_name(entry['title'], variant)} belongs to a different PDF; "
                  f"using {root / name} for book {bid}")
        work_dir = root / name
        entry["dirs"][variant] = name

    source = str(pdf_path)
    if source not in entry["sources"]:
        entry["sources"].append(source)
    _save(root, index)

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / BOOK_FILE).write_text(json.dumps(
        {"id": bid, "title": entry["title"], "variant": variant, "sources": entry["sources"]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return work_dir


def migrate(root: Path = Path("work")) -> dict:
    """Index every existing book directory from its page records. Idempotent."""
    root = Path(root)
    index = _load(root)
    added = 0
    for work_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        hashes = _hashes_in(work_dir)
        if len(hashes) != 1:
            if len(hashes) > 1:
                print(f"skipping {work_dir.name}: records from {len(hashes)} different PDFs")
            continue
        (bid,) = hashes
        name = work_dir.name
        variant = "lighton" if name.endswith("-lighton") else ""
        title = name.removesuffix("-lighton") if variant else name
        entry = index["books"].setdefault(bid, {"title": title, "dirs": {}, "sources": []})
        if entry["dirs"].get(variant) != name:
            entry["dirs"][variant] = name
            added += 1
    _save(root, index)
    print(f"{len(index['books'])} book(s) indexed in {root / INDEX_NAME} ({added} new mapping(s))")
    return index


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] not in {"migrate", "show"}:
        sys.exit("usage: python3 src/pipeline/books.py migrate|show <work_root>")
    root = Path(argv[1])
    if argv[0] == "migrate":
        migrate(root)
        return
    for bid, entry in sorted(_load(root)["books"].items()):
        dirs = ", ".join(f"{v or 'main'}: {d}" for v, d in sorted(entry["dirs"].items()))
        print(f"{bid}  {entry['title']}  [{dirs}]")


if __name__ == "__main__":
    main(sys.argv[1:])
