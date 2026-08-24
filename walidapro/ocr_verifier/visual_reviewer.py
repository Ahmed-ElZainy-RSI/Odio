from __future__ import annotations

import base64
import io
import random
import re
import time
from difflib import SequenceMatcher

import requests
from PIL import Image

from .config import SETTINGS
from .utils import extract_json_object


# ============================================================
# ARABIC HELPERS
# ============================================================

ARABIC_DIACRITICS_RE = re.compile(
    r"[\u0610-\u061A"
    r"\u064B-\u065F"
    r"\u0670"
    r"\u06D6-\u06ED]"
)


def remove_diacritics(
    text: str,
) -> str:

    return ARABIC_DIACRITICS_RE.sub(
        "",
        text or "",
    )


def normalize_arabic(
    text: str,
) -> str:
    """
    Normalization للمقارنة فقط.

    لا يستخدم لتغيير النص النهائي.
    """

    text = remove_diacritics(
        text or ""
    )

    text = text.replace(
        "ـ",
        "",
    )

    text = re.sub(
        r"[أإآٱ]",
        "ا",
        text,
    )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return text.strip()


def similarity(
    first: str,
    second: str,
) -> float:
    """
    تشابه بصري/حرفي محلي.

    مهم:
        Gemini لا يحسب هذا.
        Python يحسبه بعد رجوع القراءة.
    """

    first_norm = normalize_arabic(
        first
    )

    second_norm = normalize_arabic(
        second
    )

    if (
        not first_norm
        or not second_norm
    ):
        return 0.0

    if (
        first_norm
        == second_norm
    ):
        return 1.0

    return SequenceMatcher(
        None,
        first_norm,
        second_norm,
    ).ratio()


# ============================================================
# GEMINI VISUAL REVIEWER
# ============================================================


class GeminiVisualReviewer:
    """
    Independent Gemini Visual OCR reviewer.

    الفكرة:

        Word Crop
            ↓
        Gemini Vision
            ↓
        Reading only
            ↓
        Python compares reading
        with original/candidate

    ------------------------------------------------------------

    CRITICAL:

    Gemini NEVER receives:

        - original_word
        - candidate
        - sentence
        - context

    وبالتالي لا يمكنه اختيار كلمة
    لأنه يعرف أننا نريدها.

    مهمته الوحيدة:
        قراءة ما يراه في الصورة.

    إذا الصورة غير واضحة:
        unclear=True
    """

    def __init__(self) -> None:

        if not SETTINGS.reviewer_api_key:

            raise RuntimeError(
                "REVIEWER_API_KEY is empty."
            )

        if not SETTINGS.reviewer_model:

            raise RuntimeError(
                "REVIEWER_MODEL is empty."
            )

        if not SETTINGS.reviewer_base_url:

            raise RuntimeError(
                "REVIEWER_BASE_URL is empty."
            )

        self.session = (
            requests.Session()
        )

        self.session.headers.update(
            {
                "Authorization": (
                    f"Bearer "
                    f"{SETTINGS.reviewer_api_key}"
                ),

                "Content-Type": (
                    "application/json"
                ),
            }
        )

    # ========================================================
    # IMAGE ENCODING
    # ========================================================

    @staticmethod
    def _encode_image(
        img: Image.Image,
    ) -> str:
        """
        تحويل Crop إلى PNG Base64.

        الـCrop الصغير يتم تكبيره فقط
        لتحسين وضوح الحروف والنقاط.
        """

        image = img.convert(
            "RGB"
        )

        # ====================================================
        # UPSCALE SMALL CROPS
        # ====================================================

        if (
            max(image.size)
            < 700
        ):

            scale = min(
                4.0,
                900
                / max(
                    image.size
                ),
            )

            new_width = max(
                1,
                int(
                    image.width
                    * scale
                ),
            )

            new_height = max(
                1,
                int(
                    image.height
                    * scale
                ),
            )

            image = image.resize(
                (
                    new_width,
                    new_height,
                ),
                Image.Resampling.LANCZOS,
            )

        buffer = (
            io.BytesIO()
        )

        image.save(
            buffer,
            format="PNG",
        )

        return (
            base64
            .b64encode(
                buffer.getvalue()
            )
            .decode(
                "utf-8"
            )
        )

    # ========================================================
    # SAFE FALLBACK
    # ========================================================

    @staticmethod
    def _unclear_result() -> dict:
        """
        لو القراءة البصرية فشلت:

        ممنوع تخمين كلمة.
        """

        return {
            "reading": "",
            "confidence": 0.0,
            "unclear": True,
        }

    # ========================================================
    # REQUEST
    # ========================================================

    def _request(
        self,
        img: Image.Image,
    ) -> dict:
        """
        إرسال الصورة فقط إلى Gemini.

        لا نرسل:
            original
            candidate
            sentence
            context
        """

        encoded_image = (
            self._encode_image(
                img
            )
        )

        system_prompt = """
أنت قارئ بصري OCR متخصص في قراءة الكلمات العربية المطبوعة داخل صور صغيرة مقتصة من صفحات كتب.

مهمتك الوحيدة هي قراءة الكلمة المطبوعة فعليًا في الصورة.

هذه ليست مهمة تدقيق لغوي.
هذه ليست مهمة تصحيح سياقي.
هذه ليست مهمة تخمين.

قواعد إلزامية:

- اقرأ شكل الحروف فقط.
- راقب النقاط واتصال الحروف.
- راقب التشكيل إذا كان ظاهرًا.
- لا تستخدم المعنى.
- لا تستخدم النحو.
- لا تستخدم الصرف.
- لا تخمن كلمة شائعة.
- لا تكمل حرفًا غير واضح.
- لا تستنتج كلمة لأن شكلها يبدو قريبًا من كلمة معروفة.
- إذا ظهر جزء من كلمات مجاورة، ركز على الكلمة الموجودة في منتصف الصورة.
- إذا كان الـCrop يحتوي أكثر من كلمة ولا يمكنك تحديد الكلمة الوسطى بثقة، اجعل unclear=true.
- إذا كانت بعض الحروف غير واضحة، لا تخمنها.
- إذا لم تكن القراءة كاملة وموثوقة، اجعل unclear=true.
- عندما تكون غير متأكد، الأفضل عدم إعطاء قراءة بدل إعطاء كلمة مخمنة.

أرجع JSON فقط.

الشكل:

{
  "reading": "",
  "confidence": 0.0,
  "unclear": true
}

confidence:
من 0 إلى 1 ويقيس فقط مدى وضوح القراءة البصرية.

لا تضف Markdown.
لا تضف شرحًا.
""".strip()

        user_prompt = """
اقرأ الكلمة العربية الموجودة في منتصف الصورة قراءة بصرية حرفية فقط.

لا يوجد لديك سياق للجملة، ولا يجب أن تحتاج إليه.

إذا كنت ترى جميع الحروف بوضوح:
ضع الكلمة في reading.

إذا كانت الحروف غير واضحة أو يوجد أكثر من احتمال:
لا تخمن.

في هذه الحالة استخدم:

{
  "reading": "",
  "confidence": 0.0,
  "unclear": true
}

أرجع JSON فقط.
""".strip()

        payload = {
            "model":
                SETTINGS.reviewer_model,

            "messages": [
                {
                    "role":
                        "system",

                    "content":
                        system_prompt,
                },
                {
                    "role":
                        "user",

                    "content": [
                        {
                            "type":
                                "text",

                            "text":
                                user_prompt,
                        },
                        {
                            "type":
                                "image_url",

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

            "max_tokens": 120,
        }

        # ====================================================
        # RETRIES
        # ====================================================
        #
        # Gemini Visual مساعد فقط.
        #
        # فشل JSON لا يستحق 5 Requests.
        # ====================================================

        # Cost/safety policy:
        # One independent visual request per crop.
        # A malformed/unclear semantic answer must not trigger another paid guess.
        max_attempts = 1

        configured_timeout = float(
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
                configured_timeout,
                60.0,
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

        last_error: (
            Exception
            | None
        ) = None

        # ====================================================
        # RETRY LOOP
        # ====================================================

        for attempt in range(
            1,
            max_attempts + 1,
        ):

            try:

                response = (
                    self.session.post(
                        (
                            SETTINGS
                            .reviewer_base_url
                            .rstrip("/")
                            + "/chat/completions"
                        ),

                        json=payload,

                        timeout=timeout,
                    )
                )

                status_code = (
                    response.status_code
                )

                # ============================================
                # TEMPORARY HTTP ERROR
                # ============================================

                if (
                    status_code
                    in retryable_statuses
                ):

                    raise requests.HTTPError(
                        (
                            f"HTTP "
                            f"{status_code}: "
                            f"{response.text[:500]}"
                        ),
                        response=response,
                    )

                response.raise_for_status()

                # ============================================
                # RESPONSE JSON
                # ============================================

                data = (
                    response.json()
                )

                choices = (
                    data.get(
                        "choices"
                    )
                    or []
                )

                if not choices:

                    raise RuntimeError(
                        "Gemini Vision "
                        "returned no choices."
                    )

                message = (
                    choices[0]
                    .get(
                        "message"
                    )
                    or {}
                )

                text = (
                    message.get(
                        "content"
                    )
                )

                if not text:

                    raise RuntimeError(
                        "Gemini Vision "
                        "returned empty content."
                    )

                # ============================================
                # EXTRACT JSON
                # ============================================

                parsed = (
                    extract_json_object(
                        str(text)
                    )
                )

                if not isinstance(
                    parsed,
                    dict,
                ):

                    raise ValueError(
                        "Gemini Visual "
                        "returned invalid JSON."
                    )

                # ============================================
                # MINIMUM SCHEMA
                # ============================================

                if (
                    "reading"
                    not in parsed
                ):

                    parsed[
                        "reading"
                    ] = ""

                if (
                    "confidence"
                    not in parsed
                ):

                    parsed[
                        "confidence"
                    ] = 0.0

                if (
                    "unclear"
                    not in parsed
                ):

                    parsed[
                        "unclear"
                    ] = True

                return parsed

            # =================================================
            # HTTP ERRORS
            # =================================================

            except requests.HTTPError as exc:

                last_error = exc

                code = (
                    exc.response.status_code
                    if exc.response
                    is not None
                    else None
                )

                # Permanent 4xx.
                # Don't retry.

                if (
                    code is not None
                    and 400
                    <= code
                    < 500
                    and code
                    not in retryable_statuses
                ):

                    print(
                        "[GEMINI VISUAL] "
                        "Non-retryable HTTP error "
                        f"{code} -> UNCLEAR"
                    )

                    return (
                        self._unclear_result()
                    )

            # =================================================
            # RESPONSE / NETWORK ERRORS
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

            if (
                attempt
                >= max_attempts
            ):
                break

            # =================================================
            # SHORT BACKOFF
            # =================================================

            wait_seconds = (
                min(
                    2 ** attempt,
                    4,
                )
                + random.uniform(
                    0.1,
                    0.4,
                )
            )

            print(
                "[GEMINI VISUAL RETRY] "
                f"{attempt}/"
                f"{max_attempts} "
                f"failed: "
                f"{last_error}"
            )

            print(
                "[GEMINI VISUAL RETRY] "
                f"waiting "
                f"{wait_seconds:.1f}s"
            )

            time.sleep(
                wait_seconds
            )

        # ====================================================
        # SAFE FAILURE
        # ====================================================

        print(
            "[GEMINI VISUAL] "
            "Visual evidence unavailable "
            "after retries."
        )

        print(
            "[GEMINI VISUAL] "
            "No guessing -> UNCLEAR"
        )

        return (
            self._unclear_result()
        )

    # ========================================================
    # BOOLEAN PARSER
    # ========================================================

    @staticmethod
    def _to_bool(
        value,
    ) -> bool:

        if isinstance(
            value,
            str,
        ):

            return (
                value
                .strip()
                .lower()
                in {
                    "true",
                    "1",
                    "yes",
                    "y",
                }
            )

        return bool(
            value
        )

    # ========================================================
    # PUBLIC METHOD
    # ========================================================

    def read_crop(
        self,
        img: Image.Image,
        original_word: str,
        candidate: str | None,
    ) -> tuple[
        str,
        float,
        float,
        float,
        bool,
    ]:
        """
        Gemini sees ONLY img.

        original_word و candidate
        يستخدمان بعد رجوع Gemini فقط
        لحساب similarity داخل Python.

        Returns:

            reading
            confidence
            supports_original
            supports_candidate
            unclear
        """

        # ====================================================
        # GEMINI VISUAL READING
        # ====================================================

        data = (
            self._request(
                img=img
            )
        )

        reading = str(
            data.get(
                "reading",
                "",
            )
            or ""
        ).strip()

        # ====================================================
        # CONFIDENCE
        # ====================================================

        try:

            confidence = float(
                data.get(
                    "confidence",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            confidence = 0.0

        confidence = max(
            0.0,
            min(
                1.0,
                confidence,
            ),
        )

        # ====================================================
        # UNCLEAR
        # ====================================================

        unclear = (
            self._to_bool(
                data.get(
                    "unclear",
                    True,
                )
            )
        )

        # ====================================================
        # HARD SAFETY
        # ====================================================

        if not reading:

            return (
                "",
                0.0,
                0.0,
                0.0,
                True,
            )

        # لو Gemini نفسه قال إنه مش متأكد
        # ما نحولش القراءة لدليل قوي.

        if unclear:

            confidence = min(
                confidence,
                0.49,
            )

        # ثقة أقل من 0.72:
        # القراءة البصرية ليست قوية بما يكفي لتغيير حروف كلمة.
        if confidence < 0.72:
            unclear = True

        # يجب أن تكون القراءة كلمة عربية واحدة فقط.
        # أي شرح/أكثر من token/ناتج مشوش = غير حاسم.
        clean_tokens = re.findall(
            r"[\u0621-\u063A\u0641-\u064A\u0671-\u06D3"
            r"\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]+",
            reading,
        )

        if len(clean_tokens) != 1:
            unclear = True
        else:
            reading = clean_tokens[0]

        # ====================================================
        # LOCAL PYTHON COMPARISON
        # ====================================================
        #
        # IMPORTANT:
        #
        # Gemini لم يرَ original/candidate.
        #
        # التشابه يحسب الآن فقط.
        # ====================================================

        supports_original = (
            similarity(
                reading,
                original_word,
            )
        )

        supports_candidate = 0.0

        if candidate:

            supports_candidate = (
                similarity(
                    reading,
                    candidate,
                )
            )

        # ====================================================
        # AMBIGUITY GATE
        # ====================================================
        #
        # إذا كانت القراءة قريبة من الأصل والمرشح بدرجات متقاربة،
        # فالصورة لا تحسم القرار. لا نسمح للتشابه وحده بصناعة تصحيح.
        # ====================================================

        if candidate and not unclear:
            support_gap = abs(
                supports_candidate
                - supports_original
            )

            if support_gap < 0.12:
                unclear = True

            # لكي تعتبر القراءة دليلاً على تغيير الحروف،
            # يجب أن يكون أحد الطرفين مدعومًا بصريًا بدرجة قوية.
            if max(
                supports_original,
                supports_candidate,
            ) < 0.82:
                unclear = True

        # ====================================================
        # EXTRA SAFETY FOR UNCLEAR
        # ====================================================

        if unclear:

            # القراءة غير الحاسمة ليست دليلاً للتصحيح.
            # القرار الأعلى يجب أن يرسل الكلمة للمراجعة/يحافظ على الأصل.
            supports_original = 0.0
            supports_candidate = 0.0

        # ====================================================
        # LOG
        # ====================================================

        print(
            "[GEMINI VISUAL LOCAL] "
            f"reading='{reading}' | "
            f"confidence="
            f"{confidence:.2f} | "
            f"original_support="
            f"{supports_original:.2f} | "
            f"candidate_support="
            f"{supports_candidate:.2f} | "
            f"unclear={unclear}"
        )

        return (
            reading,
            confidence,
            supports_original,
            supports_candidate,
            unclear,
        )