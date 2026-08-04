"""Run Datalab (/api/v1/convert) over every file in assets/ and save each
result to models/datalab-output-<filename>.md, matching the running-steps
convention used in run_lighton_ocr.py so outputs can be compared side by side.
"""
from pathlib import Path

from datalab_sdk import ConvertOptions, DatalabClient
from datalab_sdk.exceptions import DatalabError
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = "datalab"  # provider/model label used in output filenames
Folder_Dir = "national_geographic"
ASSETS_DIR = Path(f"assets/{Folder_Dir}")
OUTPUT_DIR = Path(f"models/{Folder_Dir}")

OUTPUT_DIR.mkdir(exist_ok=True)

client = DatalabClient()
CONVERT_OPTIONS = ConvertOptions(
    output_format="markdown",
    mode="balanced",
    paginate=True,
)


def convert_file(path: Path):
    """Return (ok, markdown_or_error_text, page_count)."""
    try:
        result = client.convert(file_path=str(path), options=CONVERT_OPTIONS)
    except DatalabError as e:
        return False, str(e), None

    if not result.success:
        return False, result.error or "Unknown error", None

    return True, result.markdown, result.page_count


def main():
    files = sorted(
        p for p in ASSETS_DIR.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )

    for path in files:
        print(f"--- {path.name} ---")
        ok, content, page_count = convert_file(path)
        out_path = OUTPUT_DIR / f"{MODEL_NAME}-output-{path.name}.md"

        if not ok:
            print(f"  FAILED: {content[:300]}")
            out_path.with_suffix(".error.txt").write_text(content)
            continue

        out_path.write_text(content or "")
        print(f"  OK: {page_count} page(s) -> {out_path}")


if __name__ == "__main__":
    main()
