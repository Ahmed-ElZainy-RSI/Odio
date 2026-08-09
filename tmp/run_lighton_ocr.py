"""Run LightOn OCR (/api/v3/parse) over every file in assets/ and save each
result to models/lighton-output-<filename>.md, matching the existing
models/datalab-output-*.md convention used elsewhere in this repo.
"""
import io
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from pypdf import PdfReader, PdfWriter

load_dotenv()

MODEL_NAME = "lighton"  # provider/model label used in output filenames
Folder_Dir = "national_geographic"
ASSETS_DIR = Path(f"assets/{Folder_Dir}")
OUTPUT_DIR = Path(f"models/{Folder_Dir}")
SYNC_PAGE_LIMIT = 15  # LightOn's /parse sync endpoint limit per request
SYNC_SIZE_LIMIT = 19 * 1024 * 1024  # stay under the API's 20MB sync limit

OUTPUT_DIR.mkdir(exist_ok=True)


def parse_bytes(content: bytes, filename: str):
    response = requests.post(
        "https://api.lighton.ai/api/v3/parse",
        headers={"Authorization": f"Bearer {os.environ['LIGHTON_API_KEY']}"},
        files={"file": (filename, content)},
    )
    return response.status_code, response


def _write_chunk(reader: PdfReader, start: int, count: int):
    """Write pages [start, start+count) to a PDF and return (end, bytes)."""
    writer = PdfWriter()
    end = min(start + count, len(reader.pages))
    for page in reader.pages[start:end]:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return end, buf.getvalue()


def split_pdf_chunks(path: Path, page_limit: int, size_limit: int):
    """Yield (filename, bytes) slices of the PDF, each within page_limit pages
    and size_limit bytes. Shrinks the page count when a chunk is still too
    large in bytes (e.g. image-heavy scanned pages)."""
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    start = 0
    while start < total_pages:
        count = min(page_limit, total_pages - start)
        end, data = _write_chunk(reader, start, count)
        while len(data) > size_limit and count > 1:
            count = max(1, count // 2)
            end, data = _write_chunk(reader, start, count)
        yield f"{path.stem}_p{start + 1}-{end}{path.suffix}", data
        start = end


def parse_file_chunked(path: Path):
    """Return (status, pages_or_response). If the doc exceeds LightOn's sync
    page or size limit, splits it into smaller PDFs and merges results."""
    status, response = parse_bytes(path.read_bytes(), path.name)
    if status == 200:
        return 200, response.json()["result"]["pages"]

    try:
        error = response.json().get("error")
    except ValueError:
        error = None

    needs_split = (status == 400 and error == "max_pages_exceeded") or (
        status == 413 and error == "file_too_large"
    )
    if not needs_split:
        return status, response

    all_pages = []
    for chunk_name, chunk_bytes in split_pdf_chunks(path, SYNC_PAGE_LIMIT, SYNC_SIZE_LIMIT):
        chunk_status, chunk_response = parse_bytes(chunk_bytes, chunk_name)
        if chunk_status != 200:
            return chunk_status, chunk_response
        all_pages.extend(chunk_response.json()["result"]["pages"])

    return 200, all_pages


def main():
    files = sorted(
        p for p in ASSETS_DIR.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

    for path in files:
        print(f"--- {path.name} ---")
        status, result = parse_file_chunked(path)
        out_path = OUTPUT_DIR / f"{MODEL_NAME}-output-{path.name}.md"

        if status != 200:
            print(f"  FAILED: HTTP {status}: {result.text[:300]}")
            out_path.with_suffix(".error.txt").write_text(
                f"HTTP {status}\n{result.text}"
            )
            continue

        pages = result
        markdown = "\n\n".join(page["markdown"] for page in pages)
        out_path.write_text(markdown)
        print(f"  OK: {len(pages)} page(s) -> {out_path}")


if __name__ == "__main__":
    main()
