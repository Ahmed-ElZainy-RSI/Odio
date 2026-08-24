from __future__ import annotations

import os


os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"

import difflib
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from paddleocr import PaddleOCR

from .config import SETTINGS
from .models import WordBox
from .utils import normalize_ar


class BBoxDetector:
    """
    PaddleOCR is used only for localization / bounding boxes.

    LightOn remains the main OCR source.

    PaddleOCR هنا دوره فقط:
    1. تحديد أماكن الكلمات.
    2. قراءة تقريبية للكلمات للمطابقة.
    3. إرجاع Bounding Box للكلمة المشكوك فيها.
    """

    def __init__(self) -> None:
        print("[BBOX] Initializing PaddleOCR mobile models...")

        self.ocr = PaddleOCR(
            lang="ar",
            ocr_version="PP-OCRv5",

            # مهم: استخدام Mobile Detector بدل Server Detector
            text_detection_model_name="PP-OCRv5_mobile_det",

            # Arabic recognition model
            text_recognition_model_name="arabic_PP-OCRv5_mobile_rec",

            device=SETTINGS.paddle_device,

            # تعطيل oneDNN لتفادي مشاكل Windows/CPU
            enable_mkldnn=False,

            # مش محتاجين مراحل إضافية
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

        print("[BBOX] PaddleOCR initialized successfully")

    @staticmethod
    def _result_to_dict(res: Any) -> dict:
        """
        تحويل PaddleOCR Result إلى dict
        بطريقة متوافقة مع PaddleOCR 3.x.
        """

        if isinstance(res, dict):
            return res.get("res", res)

        for attr in ("json", "res"):
            value = getattr(res, attr, None)

            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = None

            if isinstance(value, dict):
                return value.get("res", value)

        save_method = getattr(res, "save_to_json", None)

        if save_method is None:
            raise RuntimeError(
                "PaddleOCR result does not expose JSON-compatible output."
            )

        with tempfile.TemporaryDirectory() as td:
            save_method(td)

            files = list(Path(td).glob("*.json"))

            if not files:
                raise RuntimeError(
                    "PaddleOCR returned a result that could not be converted to JSON."
                )

            data = json.loads(
                files[0].read_text(encoding="utf-8")
            )

            return data.get("res", data)

    def detect(
        self,
        image: Image.Image,
    ) -> list[WordBox]:
        """
        تشغيل PaddleOCR على الصفحة وإرجاع
        الكلمات مع Bounding Boxes.
        """

        print(
            f"[BBOX] Running PaddleOCR detection "
            f"on image {image.width}x{image.height}..."
        )

        arr = np.asarray(
            image.convert("RGB")
        )

        try:
            results = self.ocr.predict(arr)

        except Exception as exc:
            raise RuntimeError(
                f"PaddleOCR detection failed: {exc}"
            ) from exc

        print("[BBOX] PaddleOCR prediction completed")

        boxes: list[WordBox] = []

        for result in results:
            data = self._result_to_dict(result)

            texts = list(
                data.get("rec_texts", [])
            )

            scores = list(
                data.get("rec_scores", [])
            )

            rec_boxes = data.get("rec_boxes")

            if rec_boxes is None:
                continue

            rec_boxes = np.asarray(rec_boxes)

            for i, text in enumerate(texts):

                if i >= len(rec_boxes):
                    break

                clean_text = str(text).strip()

                if not clean_text:
                    continue

                raw = rec_boxes[i].tolist()

                if len(raw) != 4:
                    continue

                try:
                    x1, y1, x2, y2 = [
                        int(float(v))
                        for v in raw
                    ]

                except (TypeError, ValueError):
                    continue

                if x2 <= x1 or y2 <= y1:
                    continue

                score = (
                    float(scores[i])
                    if i < len(scores)
                    else 0.0
                )

                boxes.append(
                    WordBox(
                        text=clean_text,
                        score=score,
                        bbox=(x1, y1, x2, y2),
                    )
                )

        print(
            f"[BBOX] {len(boxes)} word boxes detected"
        )

        return boxes

    @staticmethod
    def find_best(
        target: str,
        boxes: list[WordBox],
    ) -> tuple[WordBox | None, float]:
        """
        البحث عن أفضل Bounding Box للكلمة المطلوبة.

        يعتمد على:
        - Arabic normalization
        - Exact match
        - Fuzzy matching
        - Paddle confidence score
        """

        normalized_target = normalize_ar(target)

        if not normalized_target:
            return None, 0.0

        best_box: WordBox | None = None
        best_score = 0.0

        for box in boxes:

            normalized_box = normalize_ar(
                box.text
            )

            if not normalized_box:
                continue

            # Exact match بعد التطبيع
            if normalized_target == normalized_box:
                return box, 1.0

            similarity = difflib.SequenceMatcher(
                None,
                normalized_target,
                normalized_box,
            ).ratio()

            confidence_bonus = (
                min(
                    max(box.score, 0.0),
                    1.0,
                )
                * 0.05
            )

            final_score = min(
                similarity + confidence_bonus,
                1.0,
            )

            if final_score > best_score:
                best_box = box
                best_score = final_score

        return best_box, best_score