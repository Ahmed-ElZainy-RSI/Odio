from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from ..bbox_detector import BBoxDetector
from ..models import WordBox
from .visual_diacritics import (
    VisualDiacriticsExtractor,
    remove_diacritics,
)


ARABIC_WORD_RE = re.compile(
    r"[\u0621-\u063A"
    r"\u0641-\u064A"
    r"\u0671-\u06D3]+"
)


@dataclass
class WordDiacriticsResult:
    """
    نتيجة استخراج التشكيل لكلمة واحدة.
    """

    index: int
    reference_word: str
    visual_word: str

    bbox: tuple[
        int,
        int,
        int,
        int,
    ]

    status: str

    crop_path: str | None = None

    paddle_text: str | None = None

    alignment_score: float | None = None


class WordDiacriticsExtractor:
    """
    استخراج التشكيل الموجود في الصورة كلمة كلمة.

    الفكرة:

    corrected_text
        ↓
    Reference Arabic Words
        ↓
    PaddleOCR Bounding Boxes
        ↓
    Smart/Fuzzy Alignment
        ↓
    Crop الصحيح لكل كلمة
        ↓
    Gemini Vision
        ↓
    نقل التشكيل المرئي فقط
        ↓
    Preservation Check
        ↓
    Final Word Results


    مهم جدًا:

    لا نعتمد على:
        reference word 1 = paddle box 1

    لأن Paddle ممكن:
        - يفقد كلمة.
        - يضيف box زائد.
        - يقرأ كلمة بشكل مختلف.

    لذلك نستخدم Dynamic Programming Alignment.
    """

    def __init__(self) -> None:

        self.bbox_detector = (
            BBoxDetector()
        )

        self.visual_extractor = (
            VisualDiacriticsExtractor()
        )

    # ============================================================
    # TEXT HELPERS
    # ============================================================

    @staticmethod
    def _contains_arabic(
        text: str,
    ) -> bool:

        if not text:
            return False

        clean = remove_diacritics(
            text
        )

        return bool(
            ARABIC_WORD_RE.search(
                clean
            )
        )

    @staticmethod
    def _arabic_words(
        text: str,
    ) -> list[str]:
        """
        استخراج الكلمات العربية فقط
        بعد إزالة التشكيل.
        """

        clean = remove_diacritics(
            text
        )

        return ARABIC_WORD_RE.findall(
            clean
        )

    # ============================================================
    # NORMALIZATION
    # ============================================================

    @staticmethod
    def _normalize_word(
        text: str,
    ) -> str:
        """
        Normalization للمطابقة فقط.

        لا تستخدم لتغيير النص النهائي.

        نقوم بـ:
            - إزالة التشكيل.
            - إزالة التطويل.
            - توحيد أشكال الألف.
            - الاحتفاظ بالحروف العربية فقط.
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

        text = "".join(
            ch
            for ch in text
            if (
                "\u0621"
                <= ch
                <= "\u06FF"
            )
        )

        return text

    @classmethod
    def _similarity(
        cls,
        first: str,
        second: str,
    ) -> float:
        """
        Fuzzy similarity بين كلمتين.
        """

        a = cls._normalize_word(
            first
        )

        b = cls._normalize_word(
            second
        )

        if not a or not b:
            return 0.0

        if a == b:
            return 1.0

        return SequenceMatcher(
            None,
            a,
            b,
        ).ratio()

    # ============================================================
    # BBOX HELPERS
    # ============================================================

    @staticmethod
    def _expand_bbox(
        bbox: tuple[
            int,
            int,
            int,
            int,
        ],
        image_size: tuple[
            int,
            int,
        ],
        pad_x: int = 18,
        pad_y: int = 18,
    ) -> tuple[
        int,
        int,
        int,
        int,
    ]:

        x1, y1, x2, y2 = (
            bbox
        )

        width, height = (
            image_size
        )

        return (
            max(
                0,
                x1 - pad_x,
            ),
            max(
                0,
                y1 - pad_y,
            ),
            min(
                width,
                x2 + pad_x,
            ),
            min(
                height,
                y2 + pad_y,
            ),
        )

    # ============================================================
    # FILTER PADDLE BOXES
    # ============================================================

    @classmethod
    def _filter_arabic_boxes(
        cls,
        boxes: list[
            WordBox
        ],
    ) -> list[
        WordBox
    ]:

        output: list[
            WordBox
        ] = []

        for box in boxes:

            x1, y1, x2, y2 = (
                box.bbox
            )

            if (
                x2 <= x1
                or y2 <= y1
            ):
                continue

            if not cls._contains_arabic(
                box.text
            ):
                continue

            output.append(
                box
            )

        return output

    # ============================================================
    # SORT BBOXES IN ARABIC READING ORDER
    # ============================================================

    @staticmethod
    def _sort_boxes_reading_order(
        boxes: list[
            WordBox
        ],
    ) -> list[
        WordBox
    ]:
        """
        ترتيب Bounding Boxes:

        1. من أعلى لأسفل.
        2. داخل السطر: يمين لليسار.
        """

        if not boxes:
            return []

        sorted_by_y = sorted(
            boxes,
            key=lambda box: (
                (
                    box.bbox[1]
                    + box.bbox[3]
                )
                / 2.0
            ),
        )

        lines: list[
            list[WordBox]
        ] = []

        for box in sorted_by_y:

            center_y = (
                box.bbox[1]
                + box.bbox[3]
            ) / 2.0

            height = max(
                1,
                box.bbox[3]
                - box.bbox[1],
            )

            best_line = None

            best_distance = None

            for line in lines:

                line_centers = [
                    (
                        item.bbox[1]
                        + item.bbox[3]
                    )
                    / 2.0
                    for item
                    in line
                ]

                line_center = (
                    sum(
                        line_centers
                    )
                    / len(
                        line_centers
                    )
                )

                line_height = max(
                    max(
                        1,
                        item.bbox[3]
                        - item.bbox[1],
                    )
                    for item
                    in line
                )

                tolerance = max(
                    height,
                    line_height,
                ) * 0.55

                distance = abs(
                    center_y
                    - line_center
                )

                if (
                    distance
                    <= tolerance
                ):

                    if (
                        best_distance
                        is None
                        or distance
                        < best_distance
                    ):

                        best_line = line

                        best_distance = (
                            distance
                        )

            if best_line is None:

                lines.append(
                    [box]
                )

            else:

                best_line.append(
                    box
                )

        # ترتيب الأسطر من أعلى لأسفل
        lines.sort(
            key=lambda line: min(
                item.bbox[1]
                for item
                in line
            )
        )

        ordered: list[
            WordBox
        ] = []

        for line in lines:

            # عربي:
            # Right -> Left
            line.sort(
                key=lambda box: (
                    box.bbox[0]
                ),
                reverse=True,
            )

            ordered.extend(
                line
            )

        return ordered

    # ============================================================
    # SMART ALIGNMENT
    # ============================================================

    @classmethod
    def _align_words(
        cls,
        reference_words: list[str],
        boxes: list[WordBox],
    ) -> list[
        tuple[
            str,
            WordBox | None,
            float,
        ]
    ]:
        """
        Dynamic Programming Alignment.

        الحالات الممكنة:

        MATCH:
            كلمة مرجعية <-> Paddle Box.

        SKIP_REFERENCE:
            الكلمة موجودة في النص،
            لكن Paddle لم يجد Box لها.

        SKIP_BOX:
            Paddle أخرج Box زائد.

        النتيجة:

            (
                reference_word,
                matching_box | None,
                similarity_score,
            )

        لو Box غير موثوق:
            box=None
            status later becomes REVIEW.
        """

        n = len(
            reference_words
        )

        m = len(
            boxes
        )

        NEG_INF = (
            -10**9
        )

        # dp[i][j]:
        # أفضل score لأول i reference
        # وأول j Paddle boxes.

        dp = [
            [
                NEG_INF
                for _ in range(
                    m + 1
                )
            ]
            for _ in range(
                n + 1
            )
        ]

        action = [
            [
                None
                for _ in range(
                    m + 1
                )
            ]
            for _ in range(
                n + 1
            )
        ]

        dp[0][0] = 0.0

        # ========================================================
        # PENALTIES
        # ========================================================

        SKIP_REFERENCE = (
            -0.65
        )

        SKIP_BOX = (
            -0.55
        )

        # ========================================================
        # INITIALIZE
        # ========================================================

        for i in range(
            1,
            n + 1,
        ):

            dp[i][0] = (
                dp[
                    i - 1
                ][0]
                + SKIP_REFERENCE
            )

            action[i][0] = (
                "SKIP_REFERENCE"
            )

        for j in range(
            1,
            m + 1,
        ):

            dp[0][j] = (
                dp[0][
                    j - 1
                ]
                + SKIP_BOX
            )

            action[0][j] = (
                "SKIP_BOX"
            )

        # ========================================================
        # DP TABLE
        # ========================================================

        for i in range(
            1,
            n + 1,
        ):

            reference = (
                reference_words[
                    i - 1
                ]
            )

            for j in range(
                1,
                m + 1,
            ):

                box = (
                    boxes[
                        j - 1
                    ]
                )

                sim = (
                    cls._similarity(
                        reference,
                        box.text,
                    )
                )

                # Similarity:
                #
                # 1.0 = قوي جدًا
                # 0.7 = جيد
                # 0.5 = محتمل
                # أقل = ضعيف

                match_score = (
                    sim * 2.0
                    - 0.75
                )

                match = (
                    dp[
                        i - 1
                    ][
                        j - 1
                    ]
                    + match_score
                )

                skip_reference = (
                    dp[
                        i - 1
                    ][j]
                    + SKIP_REFERENCE
                )

                skip_box = (
                    dp[i][
                        j - 1
                    ]
                    + SKIP_BOX
                )

                best = max(
                    match,
                    skip_reference,
                    skip_box,
                )

                dp[i][j] = (
                    best
                )

                if best == match:

                    action[i][j] = (
                        "MATCH"
                    )

                elif (
                    best
                    == skip_reference
                ):

                    action[i][j] = (
                        "SKIP_REFERENCE"
                    )

                else:

                    action[i][j] = (
                        "SKIP_BOX"
                    )

        # ========================================================
        # BACKTRACK
        # ========================================================

        reversed_alignment: list[
            tuple[
                str,
                WordBox | None,
                float,
            ]
        ] = []

        i = n
        j = m

        while (
            i > 0
            or j > 0
        ):

            current_action = (
                action[i][j]
            )

            # ====================================================
            # MATCH
            # ====================================================

            if (
                i > 0
                and j > 0
                and current_action
                == "MATCH"
            ):

                reference = (
                    reference_words[
                        i - 1
                    ]
                )

                box = (
                    boxes[
                        j - 1
                    ]
                )

                sim = (
                    cls._similarity(
                        reference,
                        box.text,
                    )
                )

                # =================================================
                # LOW CONFIDENCE MATCH
                # =================================================

                # أقل من 0.45 لا نعتبر الـBox مطابقًا بما يكفي
                # حتى يدخل مرحلة التشكيل.
                if sim >= 0.45:

                    reversed_alignment.append(
                        (
                            reference,
                            box,
                            sim,
                        )
                    )

                    print(
                        "[ALIGN MATCH] "
                        f"{reference} "
                        "<-> "
                        f"{box.text} "
                        f"score="
                        f"{sim:.2f}"
                    )

                else:

                    reversed_alignment.append(
                        (
                            reference,
                            None,
                            sim,
                        )
                    )

                    print(
                        "[ALIGN REVIEW] "
                        f"{reference} "
                        "<-> "
                        f"{box.text} "
                        f"score="
                        f"{sim:.2f}"
                    )

                i -= 1
                j -= 1

            # ====================================================
            # REFERENCE WITHOUT BOX
            # ====================================================

            elif (
                i > 0
                and (
                    current_action
                    == "SKIP_REFERENCE"
                    or j == 0
                )
            ):

                reference = (
                    reference_words[
                        i - 1
                    ]
                )

                reversed_alignment.append(
                    (
                        reference,
                        None,
                        0.0,
                    )
                )

                print(
                    "[ALIGN MISSING BOX] "
                    f"{reference}"
                )

                i -= 1

            # ====================================================
            # EXTRA PADDLE BOX
            # ====================================================

            elif j > 0:

                box = (
                    boxes[
                        j - 1
                    ]
                )

                print(
                    "[ALIGN EXTRA BOX] "
                    f"{box.text}"
                )

                j -= 1

            else:

                break

        reversed_alignment.reverse()

        # ========================================================
        # SUMMARY
        # ========================================================

        matched = sum(
            1
            for (
                _,
                box,
                _,
            )
            in reversed_alignment
            if box is not None
        )

        missing = sum(
            1
            for (
                _,
                box,
                _,
            )
            in reversed_alignment
            if box is None
        )

        print()
        print(
            "=" * 60
        )

        print(
            "WORD ALIGNMENT SUMMARY"
        )

        print(
            "=" * 60
        )

        print(
            f"Reference words : "
            f"{len(reference_words)}"
        )

        print(
            f"Paddle boxes    : "
            f"{len(boxes)}"
        )

        print(
            f"Matched         : "
            f"{matched}"
        )

        print(
            f"Missing/Review  : "
            f"{missing}"
        )

        print(
            "=" * 60
        )

        return (
            reversed_alignment
        )

    # ============================================================
    # DIACRITICS CACHE
    # ============================================================

    @staticmethod
    def _cache_path(
        debug_dir: Path | None,
    ) -> Path | None:
        """
        Cache lives next to word_crops.

        The cache stores only already-validated visual results.
        This avoids paying the visual API again when rerunning the
        same page with the same reference word + bbox.
        """

        if debug_dir is None:
            return None

        return (
            debug_dir.parent
            / "word_diacritics_cache.json"
        )

    @classmethod
    def _load_cache(
        cls,
        debug_dir: Path | None,
    ) -> dict:

        path = cls._cache_path(
            debug_dir
        )

        if (
            path is None
            or not path.exists()
        ):
            return {}

        try:

            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(
                data,
                dict,
            ):
                return data

        except Exception:
            pass

        return {}

    @classmethod
    def _save_cache(
        cls,
        debug_dir: Path | None,
        data: dict,
    ) -> None:

        path = cls._cache_path(
            debug_dir
        )

        if path is None:
            return

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        tmp = path.with_suffix(
            ".tmp"
        )

        tmp.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        tmp.replace(
            path
        )

    @classmethod
    def _cache_key(
        cls,
        reference_word: str,
        box: WordBox,
        alignment_score: float,
    ) -> str:

        reference = cls._normalize_word(
            reference_word
        )

        paddle = cls._normalize_word(
            box.text
        )

        bbox = ",".join(
            str(value)
            for value in box.bbox
        )

        return (
            f"{reference}|"
            f"{paddle}|"
            f"{bbox}|"
            f"{alignment_score:.4f}"
        )

    # ============================================================
    # EXTRACT ONE WORD
    # ============================================================

    def extract_word(
        self,
        page_image: Image.Image,
        reference_word: str,
        box: WordBox,
        index: int,
        alignment_score: float,
        debug_dir: Path | None = None,
    ) -> WordDiacriticsResult:
        """
        إرسال Crop كلمة واحدة إلى Gemini Vision.
        """

        clean_reference = (
            remove_diacritics(
                reference_word
            )
        )

        # ========================================================
        # ALIGNMENT SAFETY
        # ========================================================
        #
        # التشكيل البصري يعتمد على Crop الكلمة.
        # لو الـBBox لا يطابق الكلمة المرجعية بدرجة كافية،
        # لا نرسل Crop غير موثوق إلى Gemini.
        #
        # القاعدة:
        #   uncertain crop = REVIEW
        #   no guessing
        # ========================================================

        # التشكيل البصري حساس جدًا لمكان الـCrop.
        # أقل من 0.72 نفضّل REVIEW على نقل تشكيل من كلمة مجاورة.
        MIN_DIACRITICS_ALIGNMENT = 0.72

        if alignment_score < MIN_DIACRITICS_ALIGNMENT:

            print()

            print(
                f"[WORD REVIEW] "
                f"{clean_reference} | "
                f"alignment={alignment_score:.2f} "
                f"< {MIN_DIACRITICS_ALIGNMENT:.2f}"
            )

            print(
                "[WORD REVIEW] "
                "Crop is not reliable enough "
                "for visual diacritics -> REVIEW"
            )

            return WordDiacriticsResult(
                index=index,

                reference_word=(
                    clean_reference
                ),

                visual_word=(
                    clean_reference
                ),

                bbox=(
                    box.bbox
                ),

                status="REVIEW",

                crop_path=None,

                paddle_text=(
                    box.text
                ),

                alignment_score=(
                    alignment_score
                ),
            )

        expanded_bbox = (
            self._expand_bbox(
                box.bbox,
                page_image.size,
            )
        )

        # ========================================================
        # CACHE LOOKUP
        # ========================================================

        cache = self._load_cache(
            debug_dir
        )

        cache_key = self._cache_key(
            reference_word=clean_reference,
            box=box,
            alignment_score=alignment_score,
        )

        cached = cache.get(
            cache_key
        )

        if isinstance(
            cached,
            dict,
        ):

            cached_visual = str(
                cached.get(
                    "visual_word",
                    "",
                )
                or ""
            ).strip()

            cached_status = str(
                cached.get(
                    "status",
                    "",
                )
                or ""
            ).strip()

            # Never trust a stale/bad cache entry that changes letters.
            if (
                cached_status in {
                    "OK",
                    "REVIEW",
                }
                and remove_diacritics(
                    cached_visual
                )
                == clean_reference
            ):

                print()
                print(
                    f"[WORD CACHE] "
                    f"{clean_reference} "
                    "-> reused cached visual diacritics"
                )

                return WordDiacriticsResult(
                    index=index,
                    reference_word=clean_reference,
                    visual_word=(
                        cached_visual
                        if cached_status == "OK"
                        else clean_reference
                    ),
                    bbox=expanded_bbox,
                    status=cached_status,
                    crop_path=(
                        cached.get(
                            "crop_path"
                        )
                    ),
                    paddle_text=box.text,
                    alignment_score=alignment_score,
                )

        crop = (
            page_image
            .crop(
                expanded_bbox
            )
            .convert(
                "RGB"
            )
        )

        print()

        print(
            f"[WORD {index}] "
            f"reference="
            f"{clean_reference} | "
            f"paddle="
            f"{box.text} | "
            f"align="
            f"{alignment_score:.2f} | "
            f"bbox="
            f"{expanded_bbox}"
        )

        # ========================================================
        # GEMINI VISUAL
        # ========================================================

        try:

            visual_word = (
                self.visual_extractor.extract(
                    image=crop,
                    corrected_text=(
                        clean_reference
                    ),
                )
            )

            visual_word = (
                visual_word.strip()
            )

            # ====================================================
            # PRESERVATION SAFETY
            # ====================================================
            #
            # Gemini مسموح له بتغيير التشكيل فقط.
            # لو الحروف نفسها تغيرت، نرفض النتيجة.
            # ====================================================

            visual_plain = (
                remove_diacritics(
                    visual_word
                )
            )

            if visual_plain != clean_reference:

                print(
                    f"[WORD REVIEW] "
                    f"{clean_reference} -> "
                    f"{visual_word} | "
                    "letters changed -> REVIEW"
                )

                visual_word = (
                    clean_reference
                )

                status = (
                    "REVIEW"
                )

            else:

                status = (
                    "OK"
                )

        except Exception as exc:

            print(
                f"[WORD WARNING] "
                f"{clean_reference}: "
                f"{exc}"
            )

            visual_word = (
                clean_reference
            )

            status = (
                "REVIEW"
            )

        # ========================================================
        # SAVE DEBUG
        # ========================================================

        crop_path_value = (
            None
        )

        if debug_dir is not None:

            debug_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            crop_path = (
                debug_dir
                / (
                    f"word_"
                    f"{index:04d}"
                    f".png"
                )
            )

            result_path = (
                debug_dir
                / (
                    f"word_"
                    f"{index:04d}"
                    f".txt"
                )
            )

            crop.save(
                crop_path
            )

            result_path.write_text(
                (
                    f"REFERENCE: "
                    f"{clean_reference}\n"
                    f"PADDLE: "
                    f"{box.text}\n"
                    f"ALIGNMENT SCORE: "
                    f"{alignment_score:.4f}\n"
                    f"RESULT: "
                    f"{visual_word}\n"
                    f"STATUS: "
                    f"{status}\n"
                ),
                encoding="utf-8",
            )

            crop_path_value = str(
                crop_path
            )

        # ========================================================
        # CACHE SAVE
        # ========================================================
        #
        # Save only results that passed the "letters unchanged" guard.
        # REVIEW is cached as REVIEW so a rerun does not pay again for
        # the exact same ambiguous crop.
        # ========================================================

        cache[
            cache_key
        ] = {
            "reference_word":
                clean_reference,

            "visual_word":
                visual_word,

            "status":
                status,

            "bbox":
                list(
                    expanded_bbox
                ),

            "paddle_text":
                box.text,

            "alignment_score":
                alignment_score,

            "crop_path":
                crop_path_value,
        }

        self._save_cache(
            debug_dir,
            cache,
        )

        return (
            WordDiacriticsResult(
                index=index,

                reference_word=(
                    clean_reference
                ),

                visual_word=(
                    visual_word
                ),

                bbox=(
                    expanded_bbox
                ),

                status=status,

                crop_path=(
                    crop_path_value
                ),

                paddle_text=(
                    box.text
                ),

                alignment_score=(
                    alignment_score
                ),
            )
        )

    # ============================================================
    # MISSING WORD RESULT
    # ============================================================

    @staticmethod
    def _missing_word_result(
        reference_word: str,
        index: int,
        alignment_score: float,
    ) -> WordDiacriticsResult:

        clean_reference = (
            remove_diacritics(
                reference_word
            )
        )

        print()

        print(
            f"[WORD {index}] "
            f"{clean_reference} "
            "-> NO RELIABLE BOX "
            "-> REVIEW"
        )

        return (
            WordDiacriticsResult(
                index=index,

                reference_word=(
                    clean_reference
                ),

                visual_word=(
                    clean_reference
                ),

                bbox=(
                    0,
                    0,
                    0,
                    0,
                ),

                status=(
                    "REVIEW"
                ),

                crop_path=(
                    None
                ),

                paddle_text=(
                    None
                ),

                alignment_score=(
                    alignment_score
                ),
            )
        )

    # ============================================================
    # EXTRACT PAGE
    # ============================================================

    def extract_page(
        self,
        page_image: Image.Image,
        corrected_text: str,
        debug_dir: Path | None = None,
    ) -> list[
        WordDiacriticsResult
    ]:
        """
        استخراج التشكيل كلمة كلمة من صفحة كاملة.
        """

        print()

        print(
            "[WORD DIACRITICS] "
            "Detecting words..."
        )

        # ========================================================
        # PADDLE OCR
        # ========================================================

        all_boxes = (
            self.bbox_detector.detect(
                page_image
            )
        )

        arabic_boxes = (
            self._filter_arabic_boxes(
                all_boxes
            )
        )

        ordered_boxes = (
            self._sort_boxes_reading_order(
                arabic_boxes
            )
        )

        # ========================================================
        # REFERENCE WORDS
        # ========================================================

        reference_words = (
            self._arabic_words(
                corrected_text
            )
        )

        print(
            f"[WORD DIACRITICS] "
            f"{len(all_boxes)} "
            f"total Paddle boxes"
        )

        print(
            f"[WORD DIACRITICS] "
            f"{len(ordered_boxes)} "
            f"Arabic boxes"
        )

        print(
            f"[WORD DIACRITICS] "
            f"{len(reference_words)} "
            f"reference Arabic words"
        )

        if not ordered_boxes:

            raise RuntimeError(
                "No Arabic word boxes "
                "were detected."
            )

        if not reference_words:

            raise RuntimeError(
                "No Arabic reference "
                "words were found."
            )

        # ========================================================
        # SMART ALIGNMENT
        # ========================================================

        pairs = (
            self._align_words(
                reference_words,
                ordered_boxes,
            )
        )

        results: list[
            WordDiacriticsResult
        ] = []

        # ========================================================
        # PROCESS WORDS
        # ========================================================

        for index, (
            reference_word,
            box,
            alignment_score,
        ) in enumerate(
            pairs,
            start=1,
        ):

            # ====================================================
            # NO RELIABLE BOX
            # ====================================================

            if box is None:

                result = (
                    self._missing_word_result(
                        reference_word=(
                            reference_word
                        ),

                        index=index,

                        alignment_score=(
                            alignment_score
                        ),
                    )
                )

                results.append(
                    result
                )

                continue

            # ====================================================
            # NORMAL WORD
            # ====================================================

            result = (
                self.extract_word(
                    page_image=(
                        page_image
                    ),

                    reference_word=(
                        reference_word
                    ),

                    box=box,

                    index=index,

                    alignment_score=(
                        alignment_score
                    ),

                    debug_dir=(
                        debug_dir
                    ),
                )
            )

            results.append(
                result
            )

        # ========================================================
        # FINAL SUMMARY
        # ========================================================

        ok_count = sum(
            1
            for result
            in results
            if result.status
            == "OK"
        )

        review_count = sum(
            1
            for result
            in results
            if result.status
            == "REVIEW"
        )

        print()

        print(
            "=" * 60
        )

        print(
            "WORD DIACRITICS SUMMARY"
        )

        print(
            "=" * 60
        )

        print(
            f"Total reference words : "
            f"{len(reference_words)}"
        )

        print(
            f"Processed             : "
            f"{len(results)}"
        )

        print(
            f"OK                    : "
            f"{ok_count}"
        )

        print(
            f"Review                : "
            f"{review_count}"
        )

        print(
            "=" * 60
        )

        review_rate = (
            review_count
            / max(
                len(results),
                1,
            )
        )

        if review_rate >= 0.30:

            print(
                "[DIACRITICS QUALITY GATE] "
                f"High review rate: "
                f"{review_count}/{len(results)} "
                f"({review_rate:.1%})"
            )

            print(
                "[DIACRITICS QUALITY GATE] "
                "Do not treat this page as fully verified."
            )

        return results

    # ============================================================
    # BUILD TEST WORD SEQUENCE
    # ============================================================

    @staticmethod
    def build_word_sequence(
        results: list[
            WordDiacriticsResult
        ],
    ) -> str:
        """
        Output للاختبار فقط.

        لا يعيد علامات الترقيم أو Layout.
        """

        words = [
            result.visual_word
            for result
            in results
        ]

        return " ".join(
            words
        )