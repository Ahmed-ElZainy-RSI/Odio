from __future__ import annotations

import base64
import io
import random
import re
import time
import unicodedata

import requests
from PIL import Image

from ..config import SETTINGS


# ============================================================
# ARABIC DIACRITICS
# ============================================================

ARABIC_DIACRITICS_RE = re.compile(
    r"[\u0610-\u061A"
    r"\u064B-\u065F"
    r"\u0670"
    r"\u06D6-\u06ED]"
)


ARABIC_WORD_RE = re.compile(
    r"[\u0621-\u063A"
    r"\u0641-\u064A"
    r"\u0671-\u06D3]+"
)


# ============================================================
# REMOVE DIACRITICS
# ============================================================

def remove_diacritics(
    text: str,
) -> str:
    """
    إزالة علامات التشكيل فقط.

    مهم:
    لا نقوم بتوحيد الحروف.
    لا نصحح الإملاء.
    لا نغير الكلمات.
    """

    text = unicodedata.normalize(
        "NFC",
        text,
    )

    return ARABIC_DIACRITICS_RE.sub(
        "",
        text,
    )


# ============================================================
# ARABIC TOKEN PRESERVATION
# ============================================================

def arabic_tokens_without_diacritics(
    text: str,
) -> list[str]:
    """
    استخراج الكلمات العربية بعد إزالة التشكيل.

    الهدف:
    مقارنة الحروف والكلمات قبل/بعد Gemini.

    نتجاهل:
    - HTML
    - Markdown
    - spaces
    - line breaks
    - English notes

    لكن لو Gemini غير حرف أو كلمة عربية:
    الاختبار يفشل.
    """

    clean = remove_diacritics(
        text
    )

    return ARABIC_WORD_RE.findall(
        clean
    )


# ============================================================
# VISUAL DIACRITICS EXTRACTOR
# ============================================================

class VisualDiacriticsExtractor:
    """
    Gemini Vision Visual Diacritics Extractor.

    Input:
        1. الصورة الأصلية.
        2. النص الصحيح الناتج من OCR Verification.

    Output:
        نفس النص مع التشكيل الموجود بصريًا في الصورة فقط.

    مهم جدًا:

    Gemini ممنوع عليه:

        - تغيير الكلمات.
        - تغيير الحروف.
        - تصحيح OCR.
        - التصحيح اللغوي.
        - إعادة الصياغة.
        - إضافة تشكيل من قواعد اللغة.
        - تخمين حركة غير واضحة.

    الصورة هي المصدر الوحيد للتشكيل.
    """

    def __init__(self) -> None:

        self.api_key = (
            SETTINGS.reviewer_api_key
        )

        self.base_url = (
            SETTINGS
            .reviewer_base_url
            .rstrip("/")
        )

        self.model = (
            SETTINGS.reviewer_model
        )

        if not self.api_key:

            raise RuntimeError(
                "REVIEWER_API_KEY is empty."
            )

        if not self.base_url:

            raise RuntimeError(
                "REVIEWER_BASE_URL is empty."
            )

        if not self.model:

            raise RuntimeError(
                "REVIEWER_MODEL is empty."
            )

        self.session = (
            requests.Session()
        )

        self.session.headers.update({
            "Authorization": (
                f"Bearer "
                f"{self.api_key}"
            ),
            "Content-Type": (
                "application/json"
            ),
        })

    # ========================================================
    # IMAGE ENCODING
    # ========================================================

    @staticmethod
    def _encode_image(
        image: Image.Image,
    ) -> str:
        """
        تحويل الصفحة إلى PNG Base64.

        نحافظ على Resolution مرتفع نسبيًا
        لأن علامات التشكيل صغيرة جدًا.
        """

        img = image.convert(
            "RGB"
        )

        max_side = 2800

        if max(img.size) > max_side:

            ratio = (
                max_side
                / max(img.size)
            )

            new_width = int(
                img.width
                * ratio
            )

            new_height = int(
                img.height
                * ratio
            )

            img = img.resize(
                (
                    new_width,
                    new_height,
                ),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()

        img.save(
            buffer,
            format="PNG",
        )

        encoded = (
            base64.b64encode(
                buffer.getvalue()
            )
            .decode(
                "utf-8"
            )
        )

        return encoded

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    @staticmethod
    def _system_prompt() -> str:
        """
        Prompt مصمم لمنع أي تشكيل استنتاجي.
        """

        return """
أنت نظام رؤية OCR متخصص حصريًا في نسخ علامات التشكيل العربية المطبوعة من صورة أصلية.

هذه ليست مهمة تشكيل لغوي.

هذه ليست مهمة تدقيق لغوي.

هذه ليست مهمة تصحيح OCR.

هذه ليست مهمة إعادة صياغة.

هذه ليست مهمة استنتاج نطق الكلمات.

الصورة هي المصدر الوحيد للحركات.

ستستلم:

1. صورة صفحة عربية أصلية.
2. نصًا مرجعيًا يحتوي الحروف والكلمات الصحيحة.

استخدم النص المرجعي فقط لتحديد ترتيب الكلمات والحروف الموجودة في الصفحة.

ممنوع استخدام النص المرجعي لتخمين التشكيل.

قواعد إلزامية صارمة:

- لا تغير أي حرف موجود في النص المرجعي.
- لا تصحح أي حرف.
- لا تغير أي كلمة.
- لا تستبدل أي كلمة.
- لا تحذف أي كلمة.
- لا تضف أي كلمة.
- لا تصحح الإملاء.
- لا تعيد صياغة الجملة.
- لا تغير الأرقام.
- لا تغير علامات الترقيم.
- لا تضف شرحًا.
- لا تضف ملاحظات.
- لا تضف Markdown جديدًا.

بالنسبة للتشكيل:

- أضف فقط الحركة التي تراها فعليًا في الصورة.
- إذا لم توجد حركة مطبوعة على الحرف فاترك الحرف بدون حركة.
- إذا كانت الحركة غير واضحة فلا تخمنها.
- لا تكمل تشكيل الكلمة اعتمادًا على النحو.
- لا تكمل تشكيل الكلمة اعتمادًا على الصرف.
- لا تضف حركة لأن الكلمة من المفترض لغويًا أن تحتويها.
- لا تعتمد على معنى الجملة لتخمين الحركة.

انسخ بصريًا فقط:

- الفتحة.
- الضمة.
- الكسرة.
- السكون.
- الشدة.
- تنوين الفتح.
- تنوين الضم.
- تنوين الكسر.
- الألف الخنجرية إذا كانت ظاهرة.

إذا لم تستطع رؤية حركة بثقة، اترك الحرف بدونها.

الأولوية للدقة البصرية وليس اكتمال التشكيل.

يجب أن يكون عدد الكلمات والحروف الأصلية مطابقًا تمامًا للنص المرجعي.

أرجع النص النهائي فقط.

لا تكتب أي شيء قبل النص أو بعده.
""".strip()

    # ========================================================
    # USER PROMPT
    # ========================================================

    @staticmethod
    def _user_prompt(
        corrected_text: str,
    ) -> str:

        return f"""
النص التالي هو النص المرجعي للحروف والكلمات فقط:

----- START REFERENCE TEXT -----

{corrected_text}

----- END REFERENCE TEXT -----

انظر إلى الصورة الأصلية.

انسخ علامات التشكيل التي تراها فعليًا في الصورة إلى النص المرجعي.

لا تصحح النص.

لا تغير أي حرف.

لا تغير أي كلمة.

لا تضف أي حركة غير ظاهرة.

إذا كانت حركة غير واضحة فاترك الحرف بدون تشكيل.

أرجع النص فقط.
""".strip()

    # ========================================================
    # REQUEST
    # ========================================================

    def _request(
        self,
        image: Image.Image,
        corrected_text: str,
    ) -> str:
        """
        إرسال الصورة + النص المرجعي إلى Gemini Vision.
        """

        encoded_image = (
            self._encode_image(
                image
            )
        )

        payload = {
            "model": self.model,

            "messages": [
                {
                    "role": "system",
                    "content": (
                        self._system_prompt()
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                self._user_prompt(
                                    corrected_text
                                )
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:image/png;base64,"
                                    + encoded_image
                                )
                            },
                        },
                    ],
                },
            ],

            "temperature": 0.0,

            "max_tokens": 7000,
        }

        # Cost/safety: retry only transient failures, never 5+ paid attempts.
        configured_retries = int(
            getattr(
                SETTINGS,
                "retries",
                2,
            )
            or 2
        )

        retries = max(
            1,
            min(
                configured_retries,
                2,
            ),
        )

        timeout_value = float(
            getattr(
                SETTINGS,
                "timeout",
                180,
            )
            or 180
        )

        timeout = (
            15.0,
            min(
                timeout_value,
                120.0,
            ),
        )

        retryable_statuses = {
            408,
            429,
            500,
            502,
            503,
            504,
        }

        last_error: Exception | None = None

        # ====================================================
        # RETRY LOOP
        # ====================================================

        for attempt in range(
            1,
            retries + 1,
        ):

            try:

                response = (
                    self.session.post(
                        (
                            self.base_url
                            + "/chat/completions"
                        ),
                        json=payload,
                        timeout=timeout,
                    )
                )

                status_code = (
                    response.status_code
                )

                # --------------------------------------------
                # Temporary errors
                # --------------------------------------------

                if (
                    status_code
                    in retryable_statuses
                ):

                    raise (
                        requests.HTTPError(
                            (
                                f"HTTP "
                                f"{status_code}: "
                                f"{response.text[:500]}"
                            ),
                            response=response,
                        )
                    )

                # --------------------------------------------
                # Other HTTP errors
                # --------------------------------------------

                response.raise_for_status()

                # --------------------------------------------
                # JSON RESPONSE
                # --------------------------------------------

                data = response.json()

                choices = (
                    data.get(
                        "choices"
                    )
                    or []
                )

                if not choices:

                    raise RuntimeError(
                        "Gemini Vision returned no choices."
                    )

                message = (
                    choices[0].get(
                        "message"
                    )
                    or {}
                )

                content = (
                    message.get(
                        "content"
                    )
                )

                if not content:

                    raise RuntimeError(
                        "Gemini Vision returned empty text."
                    )

                result = str(
                    content
                ).strip()

                # إزالة Code Fence لو حصل رغم التعليمات
                if result.startswith("```"):

                    result = re.sub(
                        r"^```(?:text|markdown)?\s*",
                        "",
                        result,
                    )

                    result = re.sub(
                        r"\s*```$",
                        "",
                        result,
                    )

                    result = result.strip()

                return result

            # =================================================
            # HTTP ERROR
            # =================================================

            except requests.HTTPError as exc:

                last_error = exc

                code = (
                    exc.response.status_code
                    if exc.response
                    is not None
                    else None
                )

                # أخطاء دائمة
                if (
                    code is not None
                    and 400 <= code < 500
                    and code
                    not in retryable_statuses
                ):

                    raise RuntimeError(
                        (
                            "Visual diacritics "
                            "request failed with "
                            f"HTTP {code}: "
                            f"{exc}"
                        )
                    ) from exc

            # =================================================
            # NETWORK / RESPONSE ERRORS
            # =================================================

            except (
                requests.ConnectionError,
                requests.Timeout,
                ValueError,
                KeyError,
                IndexError,
                RuntimeError,
            ) as exc:

                last_error = exc

            # =================================================
            # NO MORE RETRIES
            # =================================================

            if attempt >= retries:
                break

            # =================================================
            # BACKOFF
            # =================================================

            wait_seconds = (
                min(
                    2 ** attempt,
                    30,
                )
                + random.uniform(
                    0.25,
                    1.5,
                )
            )

            print(
                f"[VISUAL DIACRITICS RETRY] "
                f"{attempt}/{retries} "
                f"failed: "
                f"{last_error}"
            )

            print(
                f"[VISUAL DIACRITICS RETRY] "
                f"waiting "
                f"{wait_seconds:.1f}s"
            )

            time.sleep(
                wait_seconds
            )

        raise RuntimeError(
            (
                "Visual diacritics extraction "
                f"failed after "
                f"{retries} attempts. "
                f"Last error: "
                f"{last_error}"
            )
        )

    # ========================================================
    # PRESERVATION CHECK
    # ========================================================

    @staticmethod
    def _check_preservation(
        original: str,
        result: str,
    ) -> tuple[
        bool,
        list[str],
        list[str],
    ]:
        """
        التأكد أن Gemini لم يغير الكلمات العربية.

        نقارن الكلمات بعد إزالة التشكيل.

        أي اختلاف في الحروف أو ترتيب الكلمات
        يؤدي إلى FAIL.
        """

        expected_tokens = (
            arabic_tokens_without_diacritics(
                original
            )
        )

        actual_tokens = (
            arabic_tokens_without_diacritics(
                result
            )
        )

        passed = (
            expected_tokens
            == actual_tokens
        )

        return (
            passed,
            expected_tokens,
            actual_tokens,
        )

    # ========================================================
    # FIND FIRST DIFFERENCE
    # ========================================================

    @staticmethod
    def _find_first_difference(
        expected: list[str],
        actual: list[str],
    ) -> str:
        """
        للمساعدة في Debug لو Gemini غير كلمة.
        """

        max_length = max(
            len(expected),
            len(actual),
        )

        for i in range(
            max_length
        ):

            expected_word = (
                expected[i]
                if i < len(expected)
                else "<MISSING>"
            )

            actual_word = (
                actual[i]
                if i < len(actual)
                else "<MISSING>"
            )

            if (
                expected_word
                != actual_word
            ):

                return (
                    f"word_index={i} | "
                    f"expected='{expected_word}' | "
                    f"actual='{actual_word}'"
                )

        return (
            "No token-level difference found."
        )

    # ========================================================
    # PUBLIC METHOD
    # ========================================================

    def extract(
        self,
        image: Image.Image,
        corrected_text: str,
    ) -> str:
        """
        استخراج التشكيل المرئي من الصفحة.

        لا يسمح بتغيير الحروف العربية.
        """

        if not corrected_text:

            return ""

        if not corrected_text.strip():

            return corrected_text

        print(
            "[VISUAL DIACRITICS] "
            "Reading printed diacritics "
            "from image..."
        )

        # ====================================================
        # GEMINI
        # ====================================================

        result = self._request(
            image=image,
            corrected_text=corrected_text,
        )

        # ====================================================
        # PRESERVATION
        # ====================================================

        (
            preservation_passed,
            expected_tokens,
            actual_tokens,
        ) = self._check_preservation(
            corrected_text,
            result,
        )

        if not preservation_passed:

            print()

            print(
                "[VISUAL DIACRITICS] "
                "PRESERVATION FAILED"
            )

            print(
                f"[EXPECTED ARABIC WORDS] "
                f"{len(expected_tokens)}"
            )

            print(
                f"[ACTUAL ARABIC WORDS] "
                f"{len(actual_tokens)}"
            )

            difference = (
                self._find_first_difference(
                    expected_tokens,
                    actual_tokens,
                )
            )

            print(
                f"[FIRST DIFFERENCE] "
                f"{difference}"
            )

            raise RuntimeError(
                (
                    "Visual diacritics extraction "
                    "changed Arabic letters or words. "
                    "Result rejected."
                )
            )

        # ====================================================
        # SUCCESS
        # ====================================================

        print(
            "[VISUAL DIACRITICS] "
            "PRESERVATION PASS"
        )

        print(
            f"[VISUAL DIACRITICS] "
            f"{len(expected_tokens)} "
            f"Arabic words preserved"
        )

        print(
            "[VISUAL DIACRITICS] "
            "Extraction completed"
        )

        return result