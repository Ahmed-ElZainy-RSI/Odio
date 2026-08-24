from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image

from .bbox_detector import BBoxDetector
from .config import SETTINGS
from .cropper import save_crops
from .decision import decide, visual_similarity_score
from .lighton_ocr import LightOnOCR
from .models import SuspiciousWord
from .pdf_render import render_pdf
from .reviewer import ContextReviewer
from .utils import atomic_write_text, normalize_ar, tokens
from .visual_reviewer import GeminiVisualReviewer


class OCRVerificationPipeline:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        (self.output_dir / "pages").mkdir(
            exist_ok=True,
        )

        (self.output_dir / "crops").mkdir(
            exist_ok=True,
        )

        self.lighton = LightOnOCR()
        self.reviewer = ContextReviewer()
        self.visual_reviewer = GeminiVisualReviewer()
        self.bbox = BBoxDetector()

        self.all_records: list[SuspiciousWord] = []

        self.original_pages: dict[int, str] = {}
        self.corrected_pages: dict[int, str] = {}

        self.total_words = 0

        self.current_dpi: int | None = None
        self.current_pdf_sha256: str = ""

    # ============================================================
    # CACHE
    # ============================================================

    def _page_cache(
        self,
        page: int,
    ) -> Path:

        return (
            self.output_dir
            / "pages"
            / f"page_{page:04d}.json"
        )

    def _ocr_cache_fingerprint(
        self,
    ) -> dict:

        return {
            "pdf_sha256":
                self.current_pdf_sha256,

            "dpi":
                int(
                    self.current_dpi
                    or 0
                ),

            "model":
                str(
                    getattr(
                        SETTINGS,
                        "lighton_model",
                        getattr(
                            SETTINGS,
                            "ocr_model",
                            "",
                        ),
                    )
                    or ""
                ),

            "max_side":
                int(
                    getattr(
                        SETTINGS,
                        "max_side",
                        0,
                    )
                    or 0
                ),
        }

    def _load_cached_ocr(
        self,
        page: int,
    ) -> str | None:

        path = self._page_cache(page)

        if not path.exists():
            return None

        try:
            data = json.loads(
                path.read_text(
                    encoding="utf-8",
                )
            )

            if (
                data.get("ocr_text")
                and data.get("fingerprint")
                == self._ocr_cache_fingerprint()
            ):
                return str(
                    data["ocr_text"]
                )

        except Exception:
            return None

        return None

    def _save_page_cache(
        self,
        page: int,
        text: str,
    ) -> None:

        payload = {
            "page": page,
            "ocr_text": text,
            "fingerprint":
                self._ocr_cache_fingerprint(),
        }

        atomic_write_text(
            self._page_cache(page),
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
        )


    def _visual_cache_path(
        self,
        page: int,
    ) -> Path:

        cache_dir = (
            self.output_dir
            / "visual_cache"
        )

        cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        return (
            cache_dir
            / f"page_{page:04d}.json"
        )

    def _load_visual_cache(
        self,
        page: int,
    ) -> dict:

        path = self._visual_cache_path(
            page
        )

        if not path.exists():
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

    def _save_visual_cache(
        self,
        page: int,
        data: dict,
    ) -> None:

        atomic_write_text(
            self._visual_cache_path(
                page
            ),
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
        )

    @classmethod
    def _visual_cache_key(
        cls,
        index: int,
        original: str,
        candidate: str,
        bbox,
    ) -> str:

        original_norm = normalize_ar(
            cls._strip_diacritics(
                original
            )
        )

        candidate_norm = normalize_ar(
            cls._strip_diacritics(
                candidate
            )
        )

        return (
            f"{index}|"
            f"{original_norm}|"
            f"{candidate_norm}|"
            f"{bbox}"
        )

    # ============================================================
    # REVIEWER CACHE
    # ============================================================

    REVIEW_CACHE_VERSION = "safe-v1-20260820"

    def _review_cache_path(
        self,
        page: int,
    ) -> Path:
        cache_dir = (
            self.output_dir
            / "review_cache"
        )
        cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
        return cache_dir / f"page_{page:04d}.json"

    def _load_review_cache(
        self,
        page: int,
    ) -> dict:
        path = self._review_cache_path(
            page
        )
        if not path.exists():
            return {
                "version": self.REVIEW_CACHE_VERSION,
                "items": {},
            }
        try:
            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
            if (
                isinstance(data, dict)
                and data.get("version")
                == self.REVIEW_CACHE_VERSION
            ):
                data.setdefault(
                    "items",
                    {},
                )
                return data
        except Exception:
            pass
        return {
            "version": self.REVIEW_CACHE_VERSION,
            "items": {},
        }

    def _save_review_cache(
        self,
        page: int,
        cache: dict,
    ) -> None:
        cache["version"] = (
            self.REVIEW_CACHE_VERSION
        )
        atomic_write_text(
            self._review_cache_path(
                page
            ),
            json.dumps(
                cache,
                ensure_ascii=False,
                indent=2,
            ),
        )

    @staticmethod
    def _hash_text(
        value: str,
    ) -> str:
        return hashlib.sha256(
            (value or "").encode(
                "utf-8"
            )
        ).hexdigest()

    # ============================================================
    # TEXT HELPERS
    # ============================================================

    @staticmethod
    def _arabic_word_pattern(
        word: str,
    ) -> re.Pattern:
        """
        Match an exact Arabic token, never a substring inside
        another Arabic word.
        """

        escaped = re.escape(
            word
        )

        return re.compile(
            rf"(?<![\u0621-\u063A\u0641-\u064A\u0671-\u06D3])"
            rf"{escaped}"
            rf"(?![\u0621-\u063A\u0641-\u064A\u0671-\u06D3])"
        )

    @classmethod
    def _locate_unique_word_span(
        cls,
        full_text: str,
        sentence: str,
        word: str,
    ) -> tuple[int, int] | None:
        """
        Locate the verified occurrence safely.

        Conditions:
        - sentence must occur exactly once in the page;
        - target word must occur exactly once inside that sentence;
        - matching uses Arabic-token boundaries.

        Otherwise return None -> caller keeps original and marks REVIEW.
        """

        if not full_text or not sentence or not word:
            return None

        sentence_matches = list(
            re.finditer(
                re.escape(
                    sentence
                ),
                full_text,
            )
        )

        if len(sentence_matches) != 1:
            return None

        sentence_match = sentence_matches[0]

        word_matches = list(
            cls._arabic_word_pattern(
                word
            ).finditer(
                sentence
            )
        )

        if len(word_matches) != 1:
            return None

        local = word_matches[0]

        start = (
            sentence_match.start()
            + local.start()
        )

        end = (
            sentence_match.start()
            + local.end()
        )

        if full_text[start:end] != word:
            return None

        return (
            start,
            end,
        )

    # ============================================================
    # ARABIC / VISUAL HELPERS
    # ============================================================

    @staticmethod
    def _strip_diacritics(
        text: str,
    ) -> str:

        return re.sub(
            r"[\u0610-\u061A"
            r"\u064B-\u065F"
            r"\u0670"
            r"\u06D6-\u06ED]",
            "",
            text,
        )

    @classmethod
    def _visual_tokens(
        cls,
        text: str,
    ) -> list[str]:

        text = cls._strip_diacritics(
            text
        )

        words = re.findall(
            r"[\u0621-\u063A"
            r"\u0641-\u064A"
            r"\u066E-\u06D3]+",
            text,
        )

        return [
            word.strip()
            for word in words
            if word.strip()
        ]

    @classmethod
    def _similarity(
        cls,
        a: str,
        b: str,
    ) -> float:

        na = normalize_ar(
            cls._strip_diacritics(a)
        )

        nb = normalize_ar(
            cls._strip_diacritics(b)
        )

        if not na or not nb:
            return 0.0

        return difflib.SequenceMatcher(
            None,
            na,
            nb,
        ).ratio()

    # ============================================================
    # EXTRACT BEST WORD FROM ONE LIGHTON RESULT
    # ============================================================

    @classmethod
    def _extract_best_visual_word(
        cls,
        visual_reading: str,
        candidate: str,
        original: str,
    ) -> tuple[str, float]:

        words = cls._visual_tokens(
            visual_reading
        )

        if not words:
            return (
                visual_reading.strip(),
                0.0,
            )

        candidate_norm = normalize_ar(
            cls._strip_diacritics(
                candidate
            )
        )

        original_norm = normalize_ar(
            cls._strip_diacritics(
                original
            )
        )

        best_word = ""
        best_score = -1.0

        for word in words:

            word_norm = normalize_ar(
                cls._strip_diacritics(
                    word
                )
            )

            if not word_norm:
                continue

            candidate_score = 0.0
            original_score = 0.0

            if candidate_norm:

                candidate_score = (
                    difflib.SequenceMatcher(
                        None,
                        candidate_norm,
                        word_norm,
                    ).ratio()
                )

            if original_norm:

                original_score = (
                    difflib.SequenceMatcher(
                        None,
                        original_norm,
                        word_norm,
                    ).ratio()
                )

            # Neutral target selection:
            # لا نستخدم candidate كمرساة أقوى من original.
            # نختار الكلمة الأقرب لأي من الطرفين فقط لتحديد token الصورة.
            combined_score = max(
                candidate_score,
                original_score,
            )

            if combined_score > best_score:

                best_score = combined_score
                best_word = word

        if not best_word:

            return (
                visual_reading.strip(),
                0.0,
            )

        final_similarity = cls._similarity(
            candidate,
            best_word,
        )

        return (
            best_word,
            final_similarity,
        )

    # ============================================================
    # MULTI IMAGE-PROCESSING VISUAL CONSENSUS
    # ============================================================

    @classmethod
    def _select_best_visual_reading(
        cls,
        readings: dict[str, str],
        candidate: str,
        original: str,
    ) -> tuple[str, float, str]:
        """
        readings:

        {
            "original": "...",
            "upscaled": "...",
            "contrast": "...",
            "sharp": "...",
            "threshold": "..."
        }

        يتم:
        1. تنظيف كل قراءة.
        2. استخراج الكلمة الأقرب.
        3. تجميع القراءات المتفقة.
        4. اختيار أفضل Consensus.

        Returns:

        best_word
        visual_score
        source_description
        """

        cleaned_results: list[
            tuple[str, str, float, float]
        ] = []

        # --------------------------------------------------------
        # CLEAN EACH VARIANT
        # --------------------------------------------------------

        for (
            variant_name,
            raw_reading,
        ) in readings.items():

            if not raw_reading:
                continue

            clean_word, candidate_score = (
                cls._extract_best_visual_word(
                    raw_reading,
                    candidate,
                    original,
                )
            )

            if not clean_word:
                continue

            original_score = cls._similarity(
                original,
                clean_word,
            )

            cleaned_results.append(
                (
                    variant_name,
                    clean_word,
                    candidate_score,
                    original_score,
                )
            )

            print(
                f"[VISUAL CLEAN VARIANT] "
                f"{variant_name}: "
                f"{clean_word} | "
                f"candidate={candidate_score:.2f} "
                f"original={original_score:.2f}"
            )

        if not cleaned_results:

            return "", 0.0, "none"

        # --------------------------------------------------------
        # GROUP SAME READINGS
        # --------------------------------------------------------

        groups: dict[
            str,
            list[
                tuple[
                    str,
                    str,
                    float,
                    float,
                ]
            ],
        ] = defaultdict(list)

        for result in cleaned_results:

            (
                variant_name,
                clean_word,
                candidate_score,
                original_score,
            ) = result

            normalized = normalize_ar(
                cls._strip_diacritics(
                    clean_word
                )
            )

            if not normalized:
                continue

            groups[
                normalized
            ].append(
                result
            )

        if not groups:

            return "", 0.0, "none"

        # --------------------------------------------------------
        # SCORE EACH CONSENSUS GROUP
        # --------------------------------------------------------

        total_valid = len(
            cleaned_results
        )

        best_word = ""
        best_visual_score = 0.0
        best_group_score = -1.0
        best_sources: list[str] = []

        for (
            normalized,
            group,
        ) in groups.items():

            vote_count = len(
                group
            )

            vote_ratio = (
                vote_count
                / total_valid
            )

            avg_candidate = (
                sum(
                    item[2]
                    for item in group
                )
                / vote_count
            )

            avg_original = (
                sum(
                    item[3]
                    for item in group
                )
                / vote_count
            )

            # ----------------------------------------------------
            # Consensus Score
            #
            # أهم شيء:
            # 1. اتفاق النسخ المختلفة
            # 2. توافق القراءة مع candidate
            #
            # original موجود فقط كإشارة.
            # القرار النهائي نفسه عنده Visual Veto.
            # ----------------------------------------------------

            # Consensus selection must be neutral.
            # أهم شيء اتفاق نسخ الصورة، لا قربها من candidate تحديدًا.
            strongest_side = max(
                avg_candidate,
                avg_original,
            )

            separation = abs(
                avg_candidate
                - avg_original
            )

            consensus_score = (
                vote_ratio * 0.70
                + strongest_side * 0.20
                + separation * 0.10
            )

            representative_word = (
                group[0][1]
            )

            sources = [
                item[0]
                for item in group
            ]

            print(
                f"[VISUAL CONSENSUS] "
                f"{representative_word} | "
                f"votes={vote_count}/{total_valid} "
                f"candidate={avg_candidate:.2f} "
                f"original={avg_original:.2f} "
                f"consensus={consensus_score:.2f}"
            )

            if (
                consensus_score
                > best_group_score
            ):

                best_group_score = (
                    consensus_score
                )

                best_word = (
                    representative_word
                )

                best_visual_score = (
                    avg_candidate
                )

                best_sources = (
                    sources
                )

        source_description = (
            ",".join(
                best_sources
            )
        )

        return (
            best_word,
            max(
                0.0,
                min(
                    1.0,
                    best_visual_score,
                ),
            ),
            source_description,
        )

    # ============================================================
    # PROCESS PAGE
    # ============================================================

    def process_page(
        self,
        page_num: int,
        image: Image.Image,
    ) -> None:

        print(
            f"\n===== PAGE {page_num} ====="
        )

        # --------------------------------------------------------
        # OCR
        # --------------------------------------------------------

        ocr_text = self._load_cached_ocr(
            page_num
        )

        if ocr_text is None:

            print(
                f"[OCR] "
                f"Page {page_num} "
                f"-> LightOn"
            )

            ocr_text = (
                self.lighton.ocr_image(
                    image
                )
            )

            self._save_page_cache(
                page_num,
                ocr_text,
            )

        else:

            print(
                f"[OCR] "
                f"Page {page_num} "
                f"-> cache"
            )

        self.original_pages[
            page_num
        ] = ocr_text

        corrected = ocr_text

        # التصحيحات المقبولة
        accepted_changes: list[
            tuple[
                int,
                int,
                str,
                str,
            ]
        ] = []

        # Cached independent visual results.
        # Re-running the same page will reuse them instead of paying
        # Gemini again for the exact same word/candidate/crop.
        visual_cache = self._load_visual_cache(
            page_num
        )

        # --------------------------------------------------------
        # WORD COUNT
        # --------------------------------------------------------

        page_word_count = len(
            tokens(
                ocr_text
            )
        )

        self.total_words += (
            page_word_count
        )

        print(
            f"[REVIEW] "
            f"{page_word_count} "
            f"words found"
        )

        # --------------------------------------------------------
        # CONTEXT REVIEWER
        # --------------------------------------------------------

        review_cache = self._load_review_cache(
            page_num
        )

        suspicious_key = (
            "suspicious|"
            + self._hash_text(
                ocr_text
            )
        )

        cached_suspicious = (
            review_cache["items"].get(
                suspicious_key
            )
        )

        if isinstance(
            cached_suspicious,
            list,
        ):
            suspicious = [
                SuspiciousWord(
                    **item
                )
                for item in cached_suspicious
                if isinstance(
                    item,
                    dict,
                )
            ]
            print(
                "[REVIEW CACHE] "
                "Reused suspicious-word analysis"
            )
        else:
            suspicious = (
                self.reviewer.find_suspicious(
                    page_num,
                    ocr_text,
                )
            )
            review_cache["items"][
                suspicious_key
            ] = [
                item.to_dict()
                for item in suspicious
            ]
            self._save_review_cache(
                page_num,
                review_cache,
            )

        print(
            f"[SUSPICIOUS] "
            f"{len(suspicious)} "
            f"words"
        )

        if not suspicious:

            self.corrected_pages[
                page_num
            ] = corrected

            return

        # --------------------------------------------------------
        # PADDLE BBOX
        # --------------------------------------------------------

        boxes = self.bbox.detect(
            image
        )

        print(
            f"[BBOX] "
            f"{len(boxes)} "
            f"text regions detected"
        )

        # --------------------------------------------------------
        # EACH SUSPICIOUS WORD
        # --------------------------------------------------------

        for (
            idx,
            item,
        ) in enumerate(
            suspicious,
            1,
        ):

            # ====================================================
            # BBOX MATCHING
            # ====================================================

            best, match_score = (
                self.bbox.find_best(
                    item.word,
                    boxes,
                )
            )

            item.bbox_match_score = (
                match_score
            )

            if (
                best is None
                or match_score < 0.55
            ):

                item.status = "REVIEW"

                item.reason += (
                    "; bbox not reliably matched"
                )

                print(
                    f"[REVIEW] "
                    f"{item.word}: "
                    f"bbox match failed "
                    f"({match_score:.2f})"
                )

                self.all_records.append(
                    item
                )

                continue

            item.bbox = best.bbox
            item.bbox_text = best.text

            # ====================================================
            # SAVE CROPS + IMAGE PROCESSING
            # ====================================================

            crop_dir = (
                self.output_dir
                / "crops"
                / f"page_{page_num:04d}"
            )

            (
                word_path,
                context_path,
                variant_paths,
            ) = save_crops(
                image,
                best.bbox,
                crop_dir,
                idx,
            )

            item.word_crop = str(
                word_path.relative_to(
                    self.output_dir
                )
            )

            item.context_crop = str(
                context_path.relative_to(
                    self.output_dir
                )
            )

            print(
                f"[CROP] "
                f"{item.word} "
                f"-> {item.word_crop}"
            )

            print(
                f"[IMAGE PROCESSING] "
                f"{len(variant_paths)} "
                f"variants generated"
            )

            # ====================================================
            # CANDIDATE
            # ====================================================

            candidate = (
                item.candidate
            )

            # ====================================================
            # MULTI VARIANT VISUAL OCR
            # ====================================================

            try:

                variant_readings = (
                    self.lighton
                    .verify_crop_variants(
                        variant_paths
                    )
                )

            except Exception as exc:

                item.status = "REVIEW"

                item.reason += (
                    "; visual variants "
                    f"failed: {exc}"
                )

                self.all_records.append(
                    item
                )

                continue

            # ----------------------------------------------------
            # NO CONTEXT CANDIDATE = NO LETTER CHANGE
            # ----------------------------------------------------
            #
            # لا نختار أول token من OCR بصري كـ candidate.
            # لو ContextReviewer لم ينتج مرشحًا واضحًا، نحتفظ بالأصل
            # ونرسل الحالة للمراجعة بدل اختراع مرشح.
            # ----------------------------------------------------

            if not candidate:

                item.status = "REVIEW"

                item.reason += (
                    "; no context-supported candidate"
                )

                self.all_records.append(
                    item
                )

                print(
                    f"[REVIEW] "
                    f"{item.word}: "
                    "no reliable candidate; original preserved"
                )

                continue

            # ====================================================
            # CONSENSUS
            # ====================================================

            (
                visual_clean,
                consensus_visual_score,
                visual_sources,
            ) = (
                self._select_best_visual_reading(
                    variant_readings,
                    candidate,
                    item.word,
                )
            )

            if not visual_clean:

                item.status = "REVIEW"

                item.reason += (
                    "; visual OCR returned "
                    "no usable reading"
                )

                self.all_records.append(
                    item
                )

                continue

            item.visual_reading = (
                visual_clean
            )

            # ====================================================
            # FINAL VISUAL SCORE
            # ====================================================

            basic_visual_score = (
                visual_similarity_score(
                    candidate,
                    visual_clean,
                )
            )

            item.visual_score = max(
                basic_visual_score,
                consensus_visual_score,
            )

            print(
                f"[VISUAL BEST] "
                f"{item.word} "
                f"-> {visual_clean}"
            )

            print(
                f"[VISUAL SOURCE] "
                f"{visual_sources}"
            )

            print(
                f"[VISUAL SCORE] "
                f"{item.word} "
                f"-> {candidate} "
                f"({item.visual_score:.2f})"
            )

            # ====================================================
            # GEMINI VISUAL REVIEWER (ONLY WHEN NEEDED)
            # ====================================================
            #
            # Gemini Vision هنا مستقل بصريًا تمامًا:
            # لا يرى original ولا candidate ولا الجملة.
            #
            # لتقليل التكلفة والوقت:
            # إذا كانت قراءات LightOn المحسنة متفقة بقوة على candidate
            # من 3 نسخ أو أكثر، نتجاوز Gemini Vision.
            # في الحالات المتعارضة/غير الحاسمة نستدعي Gemini كحكم بصري ثانٍ.
            # ====================================================

            gemini_reading = ""
            gemini_confidence = 0.0
            gemini_supports_original = 0.0
            gemini_supports_candidate = 0.0
            gemini_unclear = True

            lighton_support_original = self._similarity(
                item.word,
                visual_clean,
            )

            lighton_support_candidate = self._similarity(
                candidate,
                visual_clean,
            )

            source_names = [
                source.strip()
                for source in visual_sources.split(",")
                if source.strip()
                and source.strip() != "none"
            ]

            # For a real letter change we always require one
            # independent Gemini visual read. Strong LightOn consensus
            # reduces doubt, but it is not a second independent engine.
            #
            # If candidate normalizes to the same word as original,
            # there is no letter correction to validate.
            same_letters = (
                normalize_ar(
                    self._strip_diacritics(
                        item.word
                    )
                )
                ==
                normalize_ar(
                    self._strip_diacritics(
                        candidate
                    )
                )
            )

            if same_letters:

                # This stage verifies OCR letters only.
                # Diacritics-only differences belong to the dedicated
                # visual diacritization stage.
                item.status = "REJECT"

                item.reason += (
                    "; diacritics-only candidate deferred "
                    "to visual diacritics stage"
                )

                print(
                    f"[OCR DIACRITICS BYPASS] "
                    f"{item.word}: same normalized letters; "
                    "OCR token preserved"
                )

                self.all_records.append(
                    item
                )

                continue

            else:

                cache_key = self._visual_cache_key(
                    index=idx,
                    original=item.word,
                    candidate=candidate,
                    bbox=best.bbox,
                )

                cached_visual = visual_cache.get(
                    cache_key
                )

                try:

                    if isinstance(
                        cached_visual,
                        dict,
                    ):

                        gemini_reading = str(
                            cached_visual.get(
                                "reading",
                                "",
                            )
                            or ""
                        )

                        gemini_confidence = float(
                            cached_visual.get(
                                "confidence",
                                0.0,
                            )
                            or 0.0
                        )

                        gemini_supports_original = float(
                            cached_visual.get(
                                "supports_original",
                                0.0,
                            )
                            or 0.0
                        )

                        gemini_supports_candidate = float(
                            cached_visual.get(
                                "supports_candidate",
                                0.0,
                            )
                            or 0.0
                        )

                        gemini_unclear = bool(
                            cached_visual.get(
                                "unclear",
                                True,
                            )
                        )

                        print(
                            f"[GEMINI VISUAL CACHE] "
                            f"{item.word}: reused cached visual result"
                        )

                    else:

                        with Image.open(
                            word_path
                        ) as gemini_crop:

                            (
                                gemini_reading,
                                gemini_confidence,
                                gemini_supports_original,
                                gemini_supports_candidate,
                                gemini_unclear,
                            ) = self.visual_reviewer.read_crop(
                                img=gemini_crop,
                                original_word=item.word,
                                candidate=candidate,
                            )

                        visual_cache[
                            cache_key
                        ] = {
                            "reading":
                                gemini_reading,

                            "confidence":
                                gemini_confidence,

                            "supports_original":
                                gemini_supports_original,

                            "supports_candidate":
                                gemini_supports_candidate,

                            "unclear":
                                gemini_unclear,
                        }

                        self._save_visual_cache(
                            page_num,
                            visual_cache,
                        )

                    # supports_original / supports_candidate are computed
                    # only after Gemini returns. Gemini itself sees crop only.

                    item.gemini_visual_reading = (
                        gemini_reading
                    )
                    item.gemini_visual_confidence = (
                        gemini_confidence
                    )
                    item.gemini_supports_original = (
                        gemini_supports_original
                    )
                    item.gemini_supports_candidate = (
                        gemini_supports_candidate
                    )
                    item.gemini_visual_unclear = (
                        gemini_unclear
                    )

                    print(
                        f"[GEMINI VISUAL] "
                        f"{item.word} -> "
                        f"{gemini_reading} | "
                        f"confidence={gemini_confidence:.2f} "
                        f"original={gemini_supports_original:.2f} "
                        f"candidate={gemini_supports_candidate:.2f} "
                        f"unclear={gemini_unclear}"
                    )

                except Exception as exc:
                    # Temporary Gemini Vision failure must not crash
                    # the whole page/book processing.
                    item.gemini_visual_reading = None
                    item.gemini_visual_confidence = 0.0
                    item.gemini_supports_original = 0.0
                    item.gemini_supports_candidate = 0.0
                    item.gemini_visual_unclear = True

                    gemini_reading = ""
                    gemini_confidence = 0.0
                    gemini_supports_original = 0.0
                    gemini_supports_candidate = 0.0
                    gemini_unclear = True

                    item.reason += (
                        f"; Gemini visual unavailable: {exc}"
                    )

                    print(
                        f"[GEMINI VISUAL WARNING] "
                        f"{item.word}: {exc}"
                    )

            # ====================================================
            # CONTEXT VALIDATION
            # ====================================================

            try:

                context_key = (
                    "context|"
                    + self._hash_text(
                        "\n".join(
                            [
                                item.sentence,
                                item.word,
                                candidate,
                                visual_clean,
                            ]
                        )
                    )
                )

                cached_context = (
                    review_cache["items"].get(
                        context_key
                    )
                )

                if isinstance(
                    cached_context,
                    dict,
                ):
                    item.context_score = float(
                        cached_context.get(
                            "context_score",
                            0.0,
                        )
                    )
                    validation_score = float(
                        cached_context.get(
                            "validation_score",
                            0.0,
                        )
                    )
                    print(
                        f"[CONTEXT CACHE] "
                        f"{item.word}: reused scores"
                    )
                else:
                    item.context_score = (
                        self.reviewer
                        .compare_candidate(
                            item.sentence,
                            item.word,
                            candidate,
                            visual_clean,
                        )
                    )

                    sentence_span = (
                        self._locate_unique_word_span(
                            full_text=item.sentence,
                            sentence=item.sentence,
                            word=item.word,
                        )
                    )

                    if sentence_span is None:
                        raise ValueError(
                            "Target word is ambiguous inside sentence."
                        )

                    sentence_start, sentence_end = (
                        sentence_span
                    )

                    new_sentence = (
                        item.sentence[:sentence_start]
                        + candidate
                        + item.sentence[sentence_end:]
                    )

                    validation_score = (
                        self.reviewer
                        .validate_replacement(
                            item.sentence,
                            new_sentence,
                            item.word,
                            candidate,
                        )
                    )

                    review_cache["items"][
                        context_key
                    ] = {
                        "context_score":
                            item.context_score,
                        "validation_score":
                            validation_score,
                    }
                    self._save_review_cache(
                        page_num,
                        review_cache,
                    )

            except Exception as exc:

                item.status = "REVIEW"

                item.reason += (
                    "; context validation "
                    f"failed: {exc}"
                )

                self.all_records.append(
                    item
                )

                continue

            # ====================================================
            # FINAL DECISION
            # ====================================================

            (
                item.final_score,
                item.status,
            ) = decide(
                context_score=item.context_score,
                lighton_visual_score=(
                    item.visual_score
                    or 0.0
                ),
                validation_score=validation_score,
                original=item.word,
                candidate=candidate,
                lighton_visual_reading=visual_clean,
                gemini_visual_reading=(
                    gemini_reading
                ),
                gemini_visual_confidence=(
                    gemini_confidence
                ),
                gemini_supports_original=(
                    gemini_supports_original
                ),
                gemini_supports_candidate=(
                    gemini_supports_candidate
                ),
                gemini_visual_unclear=(
                    gemini_unclear
                ),
            )

            print(
                f"[DECISION] "
                f"{item.word} "
                f"-> {candidate} | "
                f"context="
                f"{item.context_score:.2f} "
                f"lighton="
                f"{item.visual_score:.2f} "
                f"gemini="
                f"{gemini_supports_candidate:.2f} "
                f"validation="
                f"{validation_score:.2f} "
                f"final="
                f"{item.final_score:.2f} "
                f"=> {item.status}"
            )

            # ====================================================
            # STORE ACCEPT
            # ====================================================

            if (
                item.status == "ACCEPT"
                and candidate != item.word
            ):

                verified_span = (
                    self._locate_unique_word_span(
                        full_text=ocr_text,
                        sentence=item.sentence,
                        word=item.word,
                    )
                )

                if verified_span is None:

                    item.status = "REVIEW"

                    item.reason += (
                        "; exact occurrence could not be located safely"
                    )

                    print(
                        f"[REVIEW] "
                        f"{item.word}: "
                        "decision was ACCEPT but the exact page occurrence "
                        "is ambiguous; original preserved"
                    )

                else:

                    start, end = (
                        verified_span
                    )

                    item.candidate = (
                        candidate
                    )

                    accepted_changes.append(
                        (
                            start,
                            end,
                            item.word,
                            candidate,
                        )
                    )

                    print(
                        f"[ACCEPTED CORRECTION] "
                        f"{item.word} "
                        f"-> {candidate} "
                        f"span=({start},{end})"
                    )

            self.all_records.append(
                item
            )

        # ========================================================
        # APPLY ACCEPTED CORRECTIONS
        # ========================================================

        print(
            f"[APPLY] Applying "
            f"{len(accepted_changes)} "
            f"accepted corrections..."
        )

        corrected = ocr_text

        # Apply from right to left using spans from the untouched OCR text.
        # This preserves every other occurrence of repeated words.
        accepted_changes.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        for (
            start,
            end,
            old_word,
            new_word,
        ) in accepted_changes:

            if corrected[
                start:end
            ] != old_word:

                raise RuntimeError(
                    "SAFE APPLY FAILED: expected exact token "
                    f"{old_word!r} at span ({start}, {end}), "
                    f"found {corrected[start:end]!r}."
                )

            corrected = (
                corrected[:start]
                + new_word
                + corrected[end:]
            )

            print(
                f"[APPLIED] "
                f"{old_word} "
                f"-> {new_word} "
                f"span=({start},{end})"
            )

        self.corrected_pages[
            page_num
        ] = corrected

    # ============================================================
    # RUN PDF
    # ============================================================

    def run_pdf(
        self,
        pdf_path: Path,
        pages: str | None = None,
        dpi: int | None = None,
    ) -> None:

        self.current_dpi = (
            dpi
            or SETTINGS.dpi
        )

        sha256 = hashlib.sha256()

        with pdf_path.open(
            "rb"
        ) as pdf_file:

            for chunk in iter(
                lambda: pdf_file.read(
                    1024 * 1024
                ),
                b"",
            ):
                sha256.update(
                    chunk
                )

        self.current_pdf_sha256 = (
            sha256.hexdigest()
        )

        rendered = render_pdf(
            pdf_path,
            self.current_dpi,
            pages,
        )

        for (
            page_num,
            image,
        ) in rendered:

            try:

                self.process_page(
                    page_num,
                    image,
                )

            except Exception as exc:

                print(
                    f"[ERROR] "
                    f"Page {page_num}: "
                    f"{exc}"
                )

                self.original_pages.setdefault(
                    page_num,
                    f"<!-- PAGE FAILED: "
                    f"{exc} -->",
                )

                self.corrected_pages.setdefault(
                    page_num,
                    self.original_pages[
                        page_num
                    ],
                )

        self.save_outputs()

    # ============================================================
    # SAVE OUTPUTS
    # ============================================================

    def save_outputs(
        self,
    ) -> None:

        original = (
            "\n\n---\n\n".join(
                f"<!-- page {p} -->"
                f"\n\n"
                f"{self.original_pages[p]}"
                for p in sorted(
                    self.original_pages
                )
            )
        )

        corrected = (
            "\n\n---\n\n".join(
                f"<!-- page {p} -->"
                f"\n\n"
                f"{self.corrected_pages[p]}"
                for p in sorted(
                    self.corrected_pages
                )
            )
        )

        # ========================================================
        # BEFORE
        # ========================================================

        atomic_write_text(
            self.output_dir
            / "original_ocr.md",
            original,
        )

        # ========================================================
        # AFTER
        # ========================================================

        atomic_write_text(
            self.output_dir
            / "corrected_text.md",
            corrected,
        )

        # ========================================================
        # CORRECTIONS JSONL
        # ========================================================

        corrections_path = (
            self.output_dir
            / "corrections.jsonl"
        )

        with corrections_path.open(
            "w",
            encoding="utf-8",
        ) as f:

            for record in self.all_records:

                f.write(
                    json.dumps(
                        record.to_dict(),
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        # ========================================================
        # STATISTICS
        # ========================================================

        accepted = sum(
            record.status
            == "ACCEPT"
            for record
            in self.all_records
        )

        rejected = sum(
            record.status
            == "REJECT"
            for record
            in self.all_records
        )

        review = sum(
            record.status
            == "REVIEW"
            for record
            in self.all_records
        )

        report = {
            "pages": len(
                self.original_pages
            ),
            "total_words": (
                self.total_words
            ),
            "suspicious_words": len(
                self.all_records
            ),
            "accepted": accepted,
            "rejected": rejected,
            "manual_review": review,
        }

        atomic_write_text(
            self.output_dir
            / "report.json",
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
        )

        # ========================================================
        # HUMAN READABLE REPORT
        # ========================================================

        report_lines = [
            "# OCR Verification Report",
            "",
            f"Pages processed: "
            f"{report['pages']}",
            f"Total words: "
            f"{report['total_words']}",
            f"Suspicious words: "
            f"{report['suspicious_words']}",
            f"Corrections accepted: "
            f"{accepted}",
            f"Corrections rejected: "
            f"{rejected}",
            f"Needs manual review: "
            f"{review}",
            "",
            "## Accepted Corrections",
            "",
        ]

        accepted_records = [
            record
            for record in self.all_records
            if record.status == "ACCEPT"
        ]

        if accepted_records:

            for record in accepted_records:

                score = (
                    f"{record.final_score:.2f}"
                    if record.final_score
                    is not None
                    else "N/A"
                )

                report_lines.append(
                    f"- Page "
                    f"{record.page}: "
                    f"`{record.word}` "
                    f"→ "
                    f"`{record.candidate}` "
                    f"(score={score})"
                )

        else:

            report_lines.append(
                "No corrections accepted."
            )

        # ========================================================
        # MANUAL REVIEW
        # ========================================================

        report_lines += [
            "",
            "## Manual Review",
            "",
        ]

        review_records = [
            record
            for record in self.all_records
            if record.status == "REVIEW"
        ]

        if review_records:

            for record in review_records:

                candidate = (
                    record.candidate
                    or "N/A"
                )

                score = (
                    f"{record.final_score:.2f}"
                    if record.final_score
                    is not None
                    else "N/A"
                )

                report_lines.append(
                    f"- Page "
                    f"{record.page}: "
                    f"`{record.word}` "
                    f"→ "
                    f"`{candidate}` "
                    f"(score={score})"
                )

        else:

            report_lines.append(
                "No manual review items."
            )

        # ========================================================
        # REJECTED
        # ========================================================

        report_lines += [
            "",
            "## Rejected Corrections",
            "",
        ]

        rejected_records = [
            record
            for record in self.all_records
            if record.status == "REJECT"
        ]

        if rejected_records:

            for record in rejected_records:

                candidate = (
                    record.candidate
                    or "N/A"
                )

                score = (
                    f"{record.final_score:.2f}"
                    if record.final_score
                    is not None
                    else "N/A"
                )

                report_lines.append(
                    f"- Page "
                    f"{record.page}: "
                    f"`{record.word}` "
                    f"→ "
                    f"`{candidate}` "
                    f"(score={score})"
                )

        else:

            report_lines.append(
                "No rejected corrections."
            )

        # ========================================================
        # BEFORE / AFTER
        # ========================================================

        report_lines += [
            "",
            "## Before",
            "",
            "```text",
            original,
            "```",
            "",
            "## After",
            "",
            "```text",
            corrected,
            "```",
        ]

        atomic_write_text(
            self.output_dir
            / "verification_report.md",
            "\n".join(
                report_lines
            ),
        )

        # ========================================================
        # CONSOLE REPORT
        # ========================================================

        print(
            "\n"
            + "=" * 48
        )

        print(
            "OCR VERIFICATION REPORT"
        )

        print(
            "=" * 48
        )

        print(
            f"Pages processed       : "
            f"{report['pages']}"
        )

        print(
            f"Total words            : "
            f"{report['total_words']}"
        )

        print(
            f"Suspicious words       : "
            f"{report['suspicious_words']}"
        )

        print(
            f"Corrections accepted   : "
            f"{accepted}"
        )

        print(
            f"Corrections rejected   : "
            f"{rejected}"
        )

        print(
            f"Needs manual review    : "
            f"{review}"
        )

        print(
            "=" * 48
        )

        print(
            "\n[OUTPUT]"
        )

        print(
            f"Before : "
            f"{self.output_dir / 'original_ocr.md'}"
        )

        print(
            f"After  : "
            f"{self.output_dir / 'corrected_text.md'}"
        )

        print(
            f"Report : "
            f"{self.output_dir / 'verification_report.md'}"
        )