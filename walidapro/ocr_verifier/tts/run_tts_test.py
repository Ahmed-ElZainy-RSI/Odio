from __future__ import annotations

import argparse
from pathlib import Path

from ocr_verifier.tts.pipeline import TTSPipeline


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Test Gemini TTS on Arabic children's book text"
    )

    parser.add_argument(
        "text_file",
        type=Path,
        help="Input .txt or .md file containing Arabic text",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("tts_output"),
        help="Output directory",
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=700,
        help="Maximum characters per TTS chunk",
    )

    args = parser.parse_args()

    # ============================================================
    # CHECK INPUT
    # ============================================================

    if not args.text_file.exists():
        raise SystemExit(
            f"Input file not found: {args.text_file}"
        )

    if args.text_file.suffix.lower() not in {
        ".txt",
        ".md",
    }:
        raise SystemExit(
            "Input must be a .txt or .md file"
        )

    # ============================================================
    # READ TEXT
    # ============================================================

    text = args.text_file.read_text(
        encoding="utf-8"
    ).strip()

    if not text:
        raise SystemExit(
            "Input text file is empty."
        )

    # ============================================================
    # HEADER
    # ============================================================

    print()
    print("=" * 70)
    print("GEMINI ARABIC CHILDREN BOOK TTS TEST")
    print("=" * 70)

    print(
        f"[INPUT] {args.text_file}"
    )

    print(
        f"[OUTPUT] {args.output}"
    )

    print(
        f"[MAX CHARS] {args.max_chars}"
    )

    # ============================================================
    # PIPELINE
    # ============================================================

    pipeline = TTSPipeline(
        output_dir=args.output,
        max_chars=args.max_chars,
    )

    result = pipeline.run(
        text=text,
        input_name=args.text_file.stem,
    )

    # ============================================================
    # RESULT
    # ============================================================

    print()
    print("=" * 70)
    print("TEST RESULT")
    print("=" * 70)

    print(
        f"Voice      : {result.voice}"
    )

    print(
        f"Model      : {result.model}"
    )

    print(
        f"Chunks     : {result.total_chunks}"
    )

    print(
        f"Successful : {result.successful_chunks}"
    )

    print(
        f"Failed     : {result.failed_chunks}"
    )

    print(
        f"Duration   : {result.total_duration_seconds:.2f}s"
    )

    print(
        f"Final audio: {result.final_audio_path}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()