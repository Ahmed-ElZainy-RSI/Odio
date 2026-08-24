from __future__ import annotations

import argparse
from pathlib import Path

from ocr_verifier.pipeline import OCRVerificationPipeline


def main() -> None:
    p = argparse.ArgumentParser(description="Arabic OCR verification pipeline")
    p.add_argument("input", type=Path, help="Input PDF")
    p.add_argument("-o", "--output", type=Path, default=Path("output"))
    p.add_argument("--pages", help="Examples: 1-5 or 1-3,8")
    p.add_argument("--dpi", type=int, default=None)
    args = p.parse_args()

    if not args.input.exists():
        raise SystemExit(f"File not found: {args.input}")
    if args.input.suffix.lower() != ".pdf":
        raise SystemExit("This runner currently expects a PDF file")

    OCRVerificationPipeline(args.output).run_pdf(args.input, args.pages, args.dpi)


if __name__ == "__main__":
    main()
