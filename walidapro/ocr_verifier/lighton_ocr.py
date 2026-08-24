from __future__ import annotations

import re

import io
import time
from pathlib import Path

import requests
from PIL import Image

from .config import SETTINGS


class LightOnOCR:
    def __init__(self) -> None:
        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"Bearer {SETTINGS.lighton_api_key}",
        })

        self.parse_url = (
            SETTINGS.lighton_base_url.rstrip("/")
            + "/api/v3/parse"
        )

    # ============================================================
    # IMAGE PREPARATION
    # ============================================================

    @staticmethod
    def prepare_image(
        img: Image.Image,
    ) -> bytes:
        """
        تجهيز الصورة كـ PNG bytes قبل إرسالها إلى LightOn Cloud.
        """

        img = img.convert("RGB")

        if max(img.size) > SETTINGS.max_side:
            ratio = (
                SETTINGS.max_side
                / max(img.size)
            )

            img = img.resize(
                (
                    int(img.width * ratio),
                    int(img.height * ratio),
                ),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()

        img.save(
            buffer,
            format="PNG",
        )

        return buffer.getvalue()

    # ============================================================
    # MAIN OCR
    # ============================================================

    def ocr_image(
        self,
        img: Image.Image,
        prompt: str | None = None,
    ) -> str:
        """
        إرسال صورة واحدة إلى LightOn Cloud Parse API
        وإرجاع النص المستخرج.

        prompt موجود للتوافق مع الكود القديم فقط.
        Parse API لا يستخدم prompt.
        """

        image_bytes = self.prepare_image(
            img
        )

        last_error: Exception | None = None

        for attempt in range(
            1,
            SETTINGS.retries + 1,
        ):
            try:

                files = {
                    "file": (
                        "page.png",
                        image_bytes,
                        "image/png",
                    )
                }

                response = self.session.post(
                    self.parse_url,
                    files=files,
                    timeout=SETTINGS.timeout,
                )

                if (
                    response.status_code == 429
                    or response.status_code >= 500
                ):
                    raise requests.HTTPError(
                        f"HTTP "
                        f"{response.status_code}: "
                        f"{response.text[:500]}"
                    )

                response.raise_for_status()

                data = response.json()

                status = data.get(
                    "status"
                )

                if status != "completed":
                    raise RuntimeError(
                        "LightOn parse not completed. "
                        f"status={status}"
                    )

                result = (
                    data.get("result")
                    or {}
                )

                pages = (
                    result.get("pages")
                    or []
                )

                if not pages:
                    raise RuntimeError(
                        "LightOn returned no pages."
                    )

                text_parts: list[str] = []

                for page in sorted(
                    pages,
                    key=lambda x: x.get(
                        "index",
                        0,
                    ),
                ):

                    markdown = page.get(
                        "markdown",
                        "",
                    )

                    if markdown:
                        text_parts.append(
                            markdown.strip()
                        )

                final_text = (
                    "\n\n".join(
                        text_parts
                    ).strip()
                )

                if not final_text:
                    raise RuntimeError(
                        "LightOn returned empty OCR text."
                    )

                return final_text

            except Exception as exc:
                last_error = exc

                if attempt < SETTINGS.retries:

                    wait_seconds = (
                        2 ** attempt
                    )

                    print(
                        f"[LIGHTON RETRY] "
                        f"Attempt "
                        f"{attempt}/"
                        f"{SETTINGS.retries} "
                        f"failed: {exc}"
                    )

                    time.sleep(
                        wait_seconds
                    )

        raise RuntimeError(
            f"LightOn OCR failed: "
            f"{last_error}"
        )

    # ============================================================
    # CLEAN VISUAL OCR RESPONSE
    # ============================================================

    @staticmethod
    def _clean_visual_text(
        text: str,
    ) -> str:
        """
        تنظيف بسيط من Markdown والأسطر الفارغة.

        لا نقوم هنا باختيار الكلمة النهائية.
        pipeline.py سيعمل المطابقة مع candidate.
        """

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        if not lines:
            return ""

        # Parse API أحيانًا يرجع أكثر من سطر
        # نحافظ عليهم مع فصل بمسافة.
        cleaned = " ".join(
            lines
        )

        cleaned = (
            cleaned
            .replace("#", "")
            .replace("**", "")
            .replace("__", "")
            .replace("`", "")
            .strip()
        )

        return cleaned

    # ============================================================
    # VERIFY ONE CROP
    # ============================================================

    def verify_crop(
        self,
        img: Image.Image,
    ) -> str:
        """
        إعادة قراءة Crop واحد باستخدام LightOn.
        """

        text = self.ocr_image(
            img
        )

        return self._clean_visual_text(
            text
        )

    # ============================================================
    # VERIFY MULTIPLE IMAGE PROCESSING VARIANTS
    # ============================================================

    def verify_crop_variants(
        self,
        variant_paths: dict[str, Path],
    ) -> dict[str, str]:
        """
        Read image-processing variants adaptively.

        Cost policy:
        - Start with two useful variants.
        - If their normalized Arabic reading agrees, stop.
        - Otherwise add variants one by one until a reading has 2 votes.
        - Use all variants only when disagreement remains.

        This never uses the context candidate, so stopping is not biased
        toward a desired correction.
        """

        preferred_order = [
            "original",
            "upscaled",
            "contrast",
            "sharp",
            "threshold",
        ]

        ordered_names = [
            name
            for name in preferred_order
            if name in variant_paths
        ]

        ordered_names.extend(
            name
            for name in variant_paths
            if name not in ordered_names
        )

        readings: dict[str, str] = {}
        votes: dict[str, int] = {}

        def normalize_reading(value: str) -> str:
            value = re.sub(
                r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]",
                "",
                value or "",
            )
            tokens = re.findall(
                r"[\u0621-\u063A\u0641-\u064A\u0671-\u06D3]+",
                value,
            )
            if not tokens:
                return ""
            return tokens[0]

        for index, variant_name in enumerate(
            ordered_names,
            start=1,
        ):
            variant_path = variant_paths[
                variant_name
            ]

            try:
                with Image.open(
                    variant_path
                ) as img:
                    reading = self.verify_crop(
                        img
                    )

                readings[
                    variant_name
                ] = reading

                print(
                    f"[VISUAL VARIANT] "
                    f"{variant_name}: "
                    f"{reading}"
                )

                normalized = normalize_reading(
                    reading
                )

                if normalized:
                    votes[normalized] = (
                        votes.get(
                            normalized,
                            0,
                        )
                        + 1
                    )

            except Exception as exc:

                # Programming errors must never be converted into
                # an empty OCR reading.
                if isinstance(
                    exc,
                    (
                        NameError,
                        AttributeError,
                        TypeError,
                    ),
                ):
                    raise

                readings[
                    variant_name
                ] = ""

                print(
                    f"[VISUAL VARIANT ERROR] "
                    f"{variant_name}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

            # Always inspect at least two variants when available.
            if index < min(
                2,
                len(ordered_names),
            ):
                continue

            # Two independent transformed views agreeing is enough to
            # stop spending LightOn requests. Gemini remains the separate
            # visual verifier for real letter changes.
            if votes and max(
                votes.values()
            ) >= 2:
                print(
                    "[VISUAL VARIANT EARLY STOP] "
                    "Two variants agree; remaining LightOn calls skipped."
                )
                break

        return readings
