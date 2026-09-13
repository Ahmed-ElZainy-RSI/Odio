from __future__ import annotations

import uuid
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent / "api_output"

JOBS_DIR = API_ROOT / "jobs"
OCR_DIR = API_ROOT / "ocr"
DIACRITIZATION_DIR = API_ROOT / "diacritization"
TTS_DIR = API_ROOT / "tts"
UPLOADS_DIR = API_ROOT / "uploads"

for _dir in (JOBS_DIR, OCR_DIR, DIACRITIZATION_DIR, TTS_DIR, UPLOADS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def new_id() -> str:
    return uuid.uuid4().hex[:16]
