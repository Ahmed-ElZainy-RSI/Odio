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
ASSETS_DIR = Path("assets")
OUTPUT_DIR = Path("models")
SYNC_PAGE_LIMIT = 15  # LightOn's /parse sync endpoint limit per request

OUTPUT_DIR.mkdir(exist_ok=True)


def parse_bytes(content: bytes, filename: str):
    response = requests.post(
        "https://api.lighton.ai/api/v3/parse",
        headers={"Authorization": f"Bearer {os.environ['LIGHTON_API_KEY']}"},
        files={"file": (filename, content)},
    )
    return response.status_code, response


def split_pdf_chunks(path: Path, chunk_size: int):
    """Yield (filename, bytes) for each <=chunk_size-page slice of the PDF."""
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    for start in range(0, total_pages, chunk_size):
        writer = PdfWriter()
        for page in reader.pages[start:start + chunk_size]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        yield f"{path.stem}_p{start + 1}-{min(start + chunk_size, total_pages)}{path.suffix}", buf.getvalue()


def parse_file_chunked(path: Path):
    """Return (status, pages_or_response). If the doc exceeds LightOn's sync
    page limit, splits it into <=SYNC_PAGE_LIMIT-page PDFs and merges results."""
    status, response = parse_bytes(path.read_bytes(), path.name)
    if status == 200:
        return 200, response.json()["result"]["pages"]
    if status != 400 or response.json().get("error") != "max_pages_exceeded":
        return status, response

    all_pages = []
    for chunk_name, chunk_bytes in split_pdf_chunks(path, SYNC_PAGE_LIMIT):
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
