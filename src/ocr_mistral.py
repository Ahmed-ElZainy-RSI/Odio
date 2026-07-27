"""Run Mistral OCR (ocr.process) over every file in assets/<Folder_Dir>/ and
save each result to models/<Folder_Dir>/mistral-output-<filename>.md, matching
the convention used by run_lighton_ocr.py and datalab_chundra.py.

Converted from src/mistral/01_mistral_ocr.ipynb, which OCR'd a single
hardcoded file and never wrote the result anywhere.

The document is sent inline as a base64 data URL, which is what the notebook
did. Mistral caps a request at 50 MB / 1,000 pages, and base64 inflates a file
by ~33%, so the practical ceiling here is a ~37 MB PDF. Larger files need the
Files API (client.files.upload) and a signed URL instead.
"""
import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral

load_dotenv()

MODEL_NAME = "mistral"  # provider/model label used in output filenames
Folder_Dir = "national_geographic"
ASSETS_DIR = Path(f"assets/{Folder_Dir}")
OUTPUT_DIR = Path(f"models/{Folder_Dir}")
OCR_MODEL = "mistral-ocr-latest"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])


def encode_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def ocr_file(path: Path):
    """Return (ok, pages_or_error_text)."""
    try:
        response = client.ocr.process(
            model=OCR_MODEL,
            document={
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{encode_file(path)}",
            },
            include_image_base64=False,  # the .md only references figures, so skip the payload
            include_blocks=True,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    return True, response.pages


def main():
    files = sorted(
        p for p in ASSETS_DIR.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

    for path in files:
        print(f"--- {path.name} ---")
        ok, result = ocr_file(path)
        out_path = OUTPUT_DIR / f"{MODEL_NAME}-output-{path.name}.md"

        if not ok:
            print(f"  FAILED: {result[:300]}")
            out_path.with_suffix(".error.txt").write_text(result)
            continue

        pages = result
        markdown = "\n\n".join(page.markdown for page in pages)
        out_path.write_text(markdown)
        print(f"  OK: {len(pages)} page(s) -> {out_path}")


if __name__ == "__main__":
    main()
