from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


# ============================================================
# RESULT
# ============================================================


@dataclass
class FinalReviewResult:
    original_text: str
    corrected_text: str
    changed: bool
    attempts: int

    # Extra safety metadata.
    # Existing callers that only use the first four fields remain compatible.
    review_required: bool = False
    rejection_reason: str | None = None
    changed_words: int = 0
    changed_ratio: float = 0.0


# ============================================================
# INTERNAL EXCEPTION
# ============================================================


class UnsafeReviewError(RuntimeError):
    """
    Raised when the model returned a syntactically valid answer,
    but the answer is too aggressive to trust without visual evidence.

    Important:
        This is NOT retried with another API call.
        Repeating a semantic guess wastes API and can produce a different guess.
    """


# ============================================================
# CLEANERS
# ============================================================


PAGE_COMMENT_RE = re.compile(
    r"<!--\s*page\s+\d+\s*-->",
    re.IGNORECASE,
)

HTML_RE = re.compile(
    r"<[^>]+>",
    re.DOTALL,
)

NOTE_RE = re.compile(
    r"(?im)^\s*Note\s*:.*$"
)

PAGE_NUMBER_RE = re.compile(
    r"(?m)^\s*\d{1,4}\s*$"
)

ARABIC_WORD_RE = re.compile(
    r"[\u0621-\u063A"
    r"\u0641-\u064A"
    r"\u0671-\u06D3]+"
)

ARABIC_DIACRITICS_RE = re.compile(
    r"[\u0610-\u061A"
    r"\u064B-\u065F"
    r"\u0670"
    r"\u06D6-\u06ED]"
)

# A dialogue / speaker line such as:
#   سارة:
#   المعلمة:
# The final text-only reviewer must not invent or replace the speaker name.
SPEAKER_LINE_RE = re.compile(
    r"(?m)^\s*"
    r"([\u0621-\u063A\u0641-\u064A\u0671-\u06D3"
    r"\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]+)"
    r"\s*[:：]"
)


def clean_ocr_noise(
    text: str,
) -> str:
    """
    Remove non-story OCR noise only.

    Does NOT:
        - correct words
        - rewrite sentences
        - add diacritics
        - infer missing text
    """

    if not text:
        return ""

    text = PAGE_COMMENT_RE.sub(
        "",
        text,
    )

    text = HTML_RE.sub(
        "",
        text,
    )

    text = NOTE_RE.sub(
        "",
        text,
    )

    text = PAGE_NUMBER_RE.sub(
        "",
        text,
    )

    lines = [
        line.rstrip()
        for line in text.splitlines()
    ]

    cleaned_lines: list[str] = []
    previous_empty = False

    for line in lines:

        empty = (
            not line.strip()
        )

        if (
            empty
            and previous_empty
        ):
            continue

        cleaned_lines.append(
            line
        )

        previous_empty = empty

    return "\n".join(
        cleaned_lines
    ).strip()


# ============================================================
# FINAL CONTEXT REVIEWER
# ============================================================


class FinalContextReviewer:
    """
    Conservative final Arabic OCR reviewer.

    This stage is intentionally NOT a free-form corrector.

    Its job:
        Correct only small, obvious residual OCR errors after the
        initial OCR + visual verification stages.

    Core rules:
        1) UNCERTAIN = KEEP ORIGINAL
        2) Speaker/person labels are protected.
        3) Large edits are rejected and marked for visual review.
        4) Unsafe semantic output is NOT retried.
        5) Retries are only for API/transport/empty-response failures.

    This stage has no image access.
    Therefore it must never "solve" ambiguous words from context alone.
    """

    # ========================================================
    # SAFETY THRESHOLDS
    # ========================================================

    # Final review should be a residual correction stage.
    # If more than 18% of aligned words are changed, the page is too
    # uncertain for a text-only final reviewer and should go to visual review.
    MAX_CHANGED_WORD_RATIO = 0.18

    # Prevent silent paragraph deletion / large insertion.
    MIN_ARABIC_CHAR_RATIO = 0.92
    MAX_ARABIC_CHAR_RATIO = 1.08

    # Allow only small OCR split/merge differences.
    MAX_WORD_COUNT_DELTA_RATIO = 0.08

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        retries: int = 3,
    ) -> None:

        self.api_key = (
            api_key
            or os.getenv(
                "FINAL_REVIEWER_API_KEY"
            )
            or os.getenv(
                "REVIEWER_API_KEY"
            )
        )

        self.base_url = (
            base_url
            or os.getenv(
                "FINAL_REVIEWER_BASE_URL"
            )
            or os.getenv(
                "REVIEWER_BASE_URL"
            )
            or (
                "https://generativelanguage."
                "googleapis.com/v1beta/openai/"
            )
        )

        self.model = (
            model
            or os.getenv(
                "FINAL_REVIEWER_MODEL",
                "gemini-3.1-pro-preview",
            )
        )

        # API retries only.
        # Three is enough for transient failures; semantic unsafe output
        # will NOT consume the remaining attempts.
        self.retries = max(
            1,
            min(
                int(retries),
                3,
            ),
        )

        if not self.api_key:
            raise RuntimeError(
                "FINAL_REVIEWER_API_KEY "
                "is missing."
            )

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    # ========================================================
    # SYSTEM PROMPT
    # ========================================================

    @staticmethod
    def _system_prompt() -> str:

        return """
أنت مراجع نهائي محافظ جدًا لأخطاء OCR في الكتب العربية.

هذه مرحلة نهائية بعد OCR ومراجعة بصرية سابقة.
مهمتك ليست إعادة كتابة النص، بل إصلاح بقايا OCR الواضحة جدًا فقط.

قواعد إلزامية:

1. استخدم السياق لفهم الجملة فقط، وليس لاختراع كلمة.
2. صحح فقط إذا كان الخطأ واضحًا وله تصحيح واحد قوي جدًا.
3. إذا كان هناك احتمالان أو أكثر: اترك الأصل كما هو.
4. لا تجعل الجملة "أجمل" ولا "أفصح" ولا تعيد صياغتها.
5. لا تضف معلومة أو كلمة مفقودة من عندك.
6. لا تحذف كلمة أو جملة لمجرد أنها تبدو غريبة.
7. لا تغيّر أسماء الأشخاص أو المتحدثين أو أسماء الأماكن اعتمادًا على السياق.
8. أي اسم يظهر في بداية سطر متبوعًا بنقطتين مثل:
   سارة:
   المعلمة:
   يجب الحفاظ عليه حرفيًا كما دخل إليك.
9. لا تغيّر ترتيب المتحدثين أو الحوار.
10. لا تضف تشكيلًا جديدًا.
11. حافظ على التشكيل الموجود قدر الإمكان.
12. حافظ على ترتيب الكلمات والجمل وعلامات الترقيم والأسطر قدر الإمكان.
13. لا تضف Markdown أو عناوين أو شروحات.
14. لا تحاول تصحيح عدد كبير من الكلمات. إذا كان النص يحتاج تغييرات كثيرة،
    اترك الكلمات غير المؤكدة كما هي؛ فهناك مرحلة مراجعة بصرية منفصلة.
15. الكلمة الغريبة ليست دليلًا كافيًا وحده على أنها خطأ OCR.

القاعدة الأهم:
UNCERTAIN = KEEP ORIGINAL

أعد النص فقط بدون أي شرح.
""".strip()

    # ========================================================
    # USER PROMPT
    # ========================================================

    @staticmethod
    def _user_prompt(
        text: str,
    ) -> str:

        return f"""
راجع النص التالي كمراجع OCR نهائي محافظ.

صحح فقط بقايا OCR الواضحة جدًا.

ممنوع:
- التخمين من المعنى فقط.
- تغيير أسماء المتحدثين.
- إعادة الصياغة.
- تحسين الأسلوب.
- إضافة أو حذف معلومات.
- اختراع كلمة مفقودة.
- إضافة تشكيل جديد.

إذا لم تكن متأكدًا من كلمة:
اتركها كما هي.

أعد النص المصحح فقط.

النص:

{text}
""".strip()

    # ========================================================
    # RESPONSE CLEANING
    # ========================================================

    @staticmethod
    def _clean_model_response(
        text: str,
    ) -> str:

        if not text:
            return ""

        text = text.strip()

        if text.startswith("```"):

            text = re.sub(
                r"^```(?:arabic|text|markdown)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )

            text = re.sub(
                r"\s*```$",
                "",
                text,
            )

        prefixes = [
            "النص المصحح:",
            "النص المصحح",
            "التصحيح:",
            "التصحيح",
        ]

        stripped = text.strip()

        for prefix in prefixes:

            if stripped.startswith(
                prefix
            ):

                stripped = stripped[
                    len(prefix):
                ].lstrip()

                break

        return stripped.strip()

    # ========================================================
    # ARABIC HELPERS
    # ========================================================

    @staticmethod
    def _strip_diacritics(
        text: str,
    ) -> str:

        return ARABIC_DIACRITICS_RE.sub(
            "",
            text or "",
        )

    @classmethod
    def _normalize_word(
        cls,
        word: str,
    ) -> str:

        value = cls._strip_diacritics(
            word
        )

        value = unicodedata.normalize(
            "NFKC",
            value,
        )

        return value.strip()

    @staticmethod
    def _arabic_letter_count(
        text: str,
    ) -> int:

        return len(
            re.findall(
                (
                    r"[\u0621-\u063A"
                    r"\u0641-\u064A"
                    r"\u0671-\u06D3]"
                ),
                text or "",
            )
        )

    @classmethod
    def _arabic_words(
        cls,
        text: str,
    ) -> list[str]:

        return [
            cls._normalize_word(
                item
            )
            for item in ARABIC_WORD_RE.findall(
                text or ""
            )
        ]

    # ========================================================
    # SPEAKER PROTECTION
    # ========================================================

    @classmethod
    def _speaker_labels(
        cls,
        text: str,
    ) -> list[str]:

        labels: list[str] = []

        for match in SPEAKER_LINE_RE.finditer(
            text or ""
        ):

            labels.append(
                cls._normalize_word(
                    match.group(1)
                )
            )

        return labels

    @classmethod
    def _validate_speaker_labels(
        cls,
        original: str,
        corrected: str,
    ) -> None:
        """
        Text-only final review is not allowed to alter speaker labels.

        This is deliberately strict:
        if a speaker label itself is wrong, it must be resolved by the
        visual reviewer, not guessed from context.
        """

        before = cls._speaker_labels(
            original
        )

        after = cls._speaker_labels(
            corrected
        )

        if before != after:

            raise UnsafeReviewError(
                "Speaker/person labels changed. "
                f"before={before} | after={after}"
            )

    # ========================================================
    # WORD CHANGE STATS
    # ========================================================

    @classmethod
    def _word_change_stats(
        cls,
        original: str,
        corrected: str,
    ) -> tuple[
        int,
        int,
        int,
        float,
    ]:
        """
        Sequence-based change count.

        Unlike index-by-index comparison, this does not mark the whole
        remainder of a paragraph as changed when OCR split/merged one word.
        """

        original_words = cls._arabic_words(
            original
        )

        corrected_words = cls._arabic_words(
            corrected
        )

        matcher = SequenceMatcher(
            None,
            original_words,
            corrected_words,
            autojunk=False,
        )

        changed = 0

        for (
            tag,
            i1,
            i2,
            j1,
            j2,
        ) in matcher.get_opcodes():

            if tag == "equal":
                continue

            if tag == "replace":

                changed += max(
                    i2 - i1,
                    j2 - j1,
                )

            elif tag == "delete":

                changed += (
                    i2 - i1
                )

            elif tag == "insert":

                changed += (
                    j2 - j1
                )

        changed_ratio = (
            changed
            / max(
                len(original_words),
                1,
            )
        )

        return (
            len(original_words),
            len(corrected_words),
            changed,
            changed_ratio,
        )

    # ========================================================
    # SAFETY VALIDATION
    # ========================================================

    def _validate_result(
        self,
        original: str,
        corrected: str,
    ) -> tuple[int, float]:

        if not corrected:
            raise RuntimeError(
                "Final reviewer returned empty text."
            )

        original_chars = (
            self._arabic_letter_count(
                original
            )
        )

        corrected_chars = (
            self._arabic_letter_count(
                corrected
            )
        )

        if original_chars == 0:
            raise RuntimeError(
                "Input contains no Arabic text."
            )

        if corrected_chars == 0:
            raise RuntimeError(
                "Final reviewer returned no Arabic text."
            )

        # ----------------------------------------------------
        # SPEAKER / PROPER-LABEL PROTECTION
        # ----------------------------------------------------

        self._validate_speaker_labels(
            original=original,
            corrected=corrected,
        )

        # ----------------------------------------------------
        # CHARACTER SIZE GUARD
        # ----------------------------------------------------

        char_ratio = (
            corrected_chars
            / max(
                original_chars,
                1,
            )
        )

        if not (
            self.MIN_ARABIC_CHAR_RATIO
            <= char_ratio
            <= self.MAX_ARABIC_CHAR_RATIO
        ):

            raise UnsafeReviewError(
                "Final reviewer changed text size "
                "too aggressively. "
                f"Arabic length ratio={char_ratio:.3f}"
            )

        # ----------------------------------------------------
        # WORD COUNT GUARD
        # ----------------------------------------------------

        (
            before_words,
            after_words,
            changed_words,
            changed_ratio,
        ) = self._word_change_stats(
            original,
            corrected,
        )

        word_delta = abs(
            after_words
            - before_words
        )

        word_delta_ratio = (
            word_delta
            / max(
                before_words,
                1,
            )
        )

        if (
            word_delta_ratio
            > self.MAX_WORD_COUNT_DELTA_RATIO
        ):

            raise UnsafeReviewError(
                "Final reviewer added or removed "
                "too many words. "
                f"before={before_words}, "
                f"after={after_words}, "
                f"delta={word_delta}, "
                f"ratio={word_delta_ratio:.2%}"
            )

        # ----------------------------------------------------
        # CHANGE-RATIO QUALITY GATE
        # ----------------------------------------------------
        #
        # This final reviewer has NO image.
        # Large residual correction means the page needs visual evidence.
        # Never allow a text-only model to rewrite a difficult page.
        # ----------------------------------------------------

        if (
            changed_ratio
            > self.MAX_CHANGED_WORD_RATIO
        ):

            raise UnsafeReviewError(
                "Too many words changed for a text-only "
                "final reviewer. "
                f"changed={changed_words}/{before_words} "
                f"({changed_ratio:.2%}) > "
                f"{self.MAX_CHANGED_WORD_RATIO:.2%}. "
                "Visual review required."
            )

        print(
            "[FINAL REVIEW SAFETY] "
            f"Words before={before_words} | "
            f"after={after_words} | "
            f"changed={changed_words} | "
            f"change_ratio={changed_ratio:.2%} | "
            f"char_ratio={char_ratio:.3f}"
        )

        return (
            changed_words,
            changed_ratio,
        )

    # ========================================================
    # REVIEW
    # ========================================================

    def review(
        self,
        text: str,
    ) -> FinalReviewResult:

        original = clean_ocr_noise(
            text
        )

        if not original:

            raise ValueError(
                "Final reviewer input "
                "text is empty."
            )

        print()
        print(
            "=" * 72
        )

        print(
            "FINAL CONTEXT REVIEW"
        )

        print(
            "=" * 72
        )

        print(
            f"[FINAL REVIEW] "
            f"Model: {self.model}"
        )

        print(
            f"[FINAL REVIEW] "
            f"Input Arabic chars: "
            f"{self._arabic_letter_count(original)}"
        )

        print(
            f"[FINAL REVIEW] "
            f"Input Arabic words: "
            f"{len(self._arabic_words(original))}"
        )

        last_error: Exception | None = (
            None
        )

        # ====================================================
        # API ATTEMPTS
        # ====================================================
        #
        # Retry only transport/API/empty-response failures.
        # Unsafe semantic output returns immediately to safe fallback.
        # ====================================================

        for attempt in range(
            1,
            self.retries + 1,
        ):

            try:

                print(
                    f"[FINAL REVIEW] "
                    f"Attempt "
                    f"{attempt}/"
                    f"{self.retries}"
                )

                response = (
                    self.client
                    .chat
                    .completions
                    .create(
                        model=self.model,

                        messages=[
                            {
                                "role":
                                    "system",

                                "content":
                                    self._system_prompt(),
                            },
                            {
                                "role":
                                    "user",

                                "content":
                                    self._user_prompt(
                                        original
                                    ),
                            },
                        ],

                        temperature=0.0,
                    )
                )

                raw = (
                    response
                    .choices[0]
                    .message
                    .content
                    or ""
                )

                if not raw.strip():
                    raise RuntimeError(
                        "Final reviewer returned empty response."
                    )

                corrected = (
                    self._clean_model_response(
                        raw
                    )
                )

                corrected = (
                    clean_ocr_noise(
                        corrected
                    )
                )

                (
                    changed_words,
                    changed_ratio,
                ) = self._validate_result(
                    original=original,
                    corrected=corrected,
                )

                changed = (
                    corrected.strip()
                    != original.strip()
                )

                print(
                    "[FINAL REVIEW] PASS"
                )

                print(
                    f"[FINAL REVIEW] "
                    f"Changed: {changed}"
                )

                print(
                    f"[FINAL REVIEW] "
                    f"Changed words: "
                    f"{changed_words}"
                )

                print(
                    f"[FINAL REVIEW] "
                    f"Changed ratio: "
                    f"{changed_ratio:.2%}"
                )

                print(
                    f"[FINAL REVIEW] "
                    f"Output Arabic chars: "
                    f"{self._arabic_letter_count(corrected)}"
                )

                print(
                    f"[FINAL REVIEW] "
                    f"Output Arabic words: "
                    f"{len(self._arabic_words(corrected))}"
                )

                return FinalReviewResult(
                    original_text=original,
                    corrected_text=corrected,
                    changed=changed,
                    attempts=attempt,
                    review_required=False,
                    rejection_reason=None,
                    changed_words=changed_words,
                    changed_ratio=changed_ratio,
                )

            except UnsafeReviewError as exc:
                # ------------------------------------------------
                # IMPORTANT COST / SAFETY RULE
                # ------------------------------------------------
                #
                # Do NOT ask the model again for another semantic guess.
                # Keep verified input and mark page for visual review.
                # ------------------------------------------------

                last_error = exc

                print(
                    "[FINAL REVIEW UNSAFE] "
                    f"{exc}"
                )

                print(
                    "[FINAL REVIEW UNSAFE] "
                    "No semantic retry. "
                    "Keeping verified input."
                )

                return FinalReviewResult(
                    original_text=original,
                    corrected_text=original,
                    changed=False,
                    attempts=attempt,
                    review_required=True,
                    rejection_reason=str(exc),
                    changed_words=0,
                    changed_ratio=0.0,
                )

            except Exception as exc:

                last_error = exc

                print(
                    f"[FINAL REVIEW ERROR] "
                    f"{attempt}/"
                    f"{self.retries}: "
                    f"{exc}"
                )

                if attempt < self.retries:

                    wait = min(
                        2 ** attempt,
                        6,
                    )

                    print(
                        "[FINAL REVIEW RETRY] "
                        f"Waiting {wait}s..."
                    )

                    time.sleep(
                        wait
                    )

        # ====================================================
        # SAFE FALLBACK
        # ====================================================

        print(
            "[FINAL REVIEW FALLBACK] "
            "Reviewer/API result unavailable."
        )

        print(
            "[FINAL REVIEW FALLBACK] "
            "Keeping original verified text."
        )

        print(
            f"[FINAL REVIEW FALLBACK] "
            f"Last error: {last_error}"
        )

        return FinalReviewResult(
            original_text=original,
            corrected_text=original,
            changed=False,
            attempts=self.retries,
            review_required=True,
            rejection_reason=(
                str(last_error)
                if last_error
                else "Reviewer unavailable."
            ),
            changed_words=0,
            changed_ratio=0.0,
        )


# ============================================================
# SIMPLE DIRECT TEST
# ============================================================


if __name__ == "__main__":

    sample = """
فَمَكْرَبٌ فِي كَنَفِيَّةٍ مُعَاجِلَةٍ الْأَمْرِ،
فَأَخَذَتْ أَبْحَثُ عَنْ بَدِيلٍ.
وَأَجْهَرَةِ الْأَلْعَابِ الْإِلَاكْتَرُونِيَّةِ،
وَذَارَ بَيْنَهَا هَذَا الْحَوَّارُ.
""".strip()

    reviewer = (
        FinalContextReviewer()
    )

    result = reviewer.review(
        sample
    )

    print()
    print(
        "=" * 72
    )

    print(
        "ORIGINAL"
    )

    print(
        "=" * 72
    )

    print(
        result.original_text
    )

    print()
    print(
        "=" * 72
    )

    print(
        "FINAL CORRECTED TEXT"
    )

    print(
        "=" * 72
    )

    print(
        result.corrected_text
    )

    print()
    print(
        "review_required="
        f"{result.review_required}"
    )

    print(
        "rejection_reason="
        f"{result.rejection_reason}"
    )