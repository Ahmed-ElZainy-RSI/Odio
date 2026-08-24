"""Shared fixtures for the correction-pipeline test suite.

Puts src/ and tools/ on sys.path (neither is a proper installed package;
every module in this repo does its own sys.path.insert for ITS OWN imports,
but the test files themselves need both directories importable too) and
provides small builders for fake page records, so individual test files
don't each reinvent "build a token" / "write a page json".

Run with: /Users/ahmedmostafa/miniconda3/envs/vx2lm/bin/python3 -m pytest src/tests
(plain python3 lacks google-genai/fitz/faster-whisper -- tests that need
them use pytest.importorskip so they degrade to "skipped", not "erred",
under an environment missing those packages.)
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (REPO_ROOT / "src", REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest


def make_token(id_: str, txt: str, raw: str | None = None,
                fixes: list[str] | None = None, box: list[float] | None = None) -> dict:
    tok = {"id": id_, "raw": raw if raw is not None else txt, "txt": txt, "fixes": fixes or []}
    if box is not None:
        tok["box"] = box
    return tok


def write_page(dir_: Path, page_no: int, tokens: list[dict], **extra) -> Path:
    rec = {
        "book": "test-book", "page": page_no,
        "status": extra.pop("status", "spoken"),
        "box_level": extra.pop("box_level", "word"),
        "ocr_engine": extra.pop("ocr_engine", "test-engine"),
        "tokens": tokens,
    }
    rec.update(extra)
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"p{page_no:03d}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def read_page(dir_: Path, page_no: int) -> dict:
    return json.loads((dir_ / f"p{page_no:03d}.json").read_text(encoding="utf-8"))


@pytest.fixture
def gemini_dir(tmp_path) -> Path:
    d = tmp_path / "book"
    d.mkdir()
    (d / "pages").mkdir()
    return d


@pytest.fixture
def lighton_dir(tmp_path) -> Path:
    d = tmp_path / "book-lighton"
    d.mkdir()
    return d


def write_review(lighton_dir: Path, gemini_dir: Path, pages: dict[str, list[dict]]) -> Path:
    """pages: {"1": [entry, entry, ...], ...} in ensemble_review.json's own shape."""
    review = {"gemini_dir": str(gemini_dir), "lighton_dir": str(lighton_dir), "pages": pages}
    path = lighton_dir / "ensemble_review.json"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
