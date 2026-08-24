from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    lighton_base_url: str = os.getenv("LIGHTON_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    lighton_api_key: str = os.getenv("LIGHTON_API_KEY", "EMPTY")
    lighton_model: str = os.getenv("LIGHTON_MODEL", "lightonai/LightOnOCR-2-1B")

    reviewer_base_url: str = os.getenv("REVIEWER_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    reviewer_api_key: str = os.getenv("REVIEWER_API_KEY", "EMPTY")
    reviewer_model: str = os.getenv("REVIEWER_MODEL", "")

    dpi: int = int(os.getenv("OCR_DPI", "220"))
    max_side: int = int(os.getenv("LIGHTON_MAX_SIDE", "1540"))
    max_tokens: int = int(os.getenv("LIGHTON_MAX_TOKENS", "6500"))
    timeout: int = int(os.getenv("API_TIMEOUT", "180"))
    retries: int = int(os.getenv("API_RETRIES", "3"))

    suspicion_threshold: float = float(os.getenv("SUSPICION_THRESHOLD", "0.75"))
    accept_threshold: float = float(os.getenv("ACCEPT_THRESHOLD", "0.88"))
    review_threshold: float = float(os.getenv("REVIEW_THRESHOLD", "0.70"))
    padding_x: int = int(os.getenv("CROP_PADDING_X", "30"))
    padding_y: int = int(os.getenv("CROP_PADDING_Y", "20"))

    paddle_device: str = os.getenv("PADDLE_DEVICE", "cpu")


SETTINGS = Settings()
