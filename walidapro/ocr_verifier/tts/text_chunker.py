from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    """
    جزء نصي واحد سيتم إرساله إلى Gemini TTS.
    """

    index: int
    text: str
    char_count: int


class TextChunker:
    """
    تقسيم النص العربي إلى أجزاء مناسبة للـ TTS.

    الهدف:
        - عدم إرسال كتاب كامل مرة واحدة.
        - عدم قطع الجملة قدر الإمكان.
        - الحفاظ على علامات الترقيم.
        - الحفاظ على التشكيل كما هو.
        - عدم تغيير الكلمات.
    """

    def __init__(
        self,
        max_chars: int = 900,
    ) -> None:

        if max_chars < 100:
            raise ValueError(
                "max_chars must be at least 100."
            )

        self.max_chars = max_chars

    # ============================================================
    # CLEAN TEXT
    # ============================================================

    @staticmethod
    def clean_text(
        text: str,
    ) -> str:
        """
        تنظيف المسافات فقط.

        مهم:
        لا نحذف التشكيل.
        لا نغير الحروف.
        لا نصحح النص.
        """

        if not text:
            return ""

        # توحيد نهاية الأسطر
        text = text.replace(
            "\r\n",
            "\n",
        )

        text = text.replace(
            "\r",
            "\n",
        )

        # إزالة المسافات الزائدة
        # مع الحفاظ على الفقرات
        lines = []

        for line in text.splitlines():

            line = re.sub(
                r"[ \t]+",
                " ",
                line,
            ).strip()

            if line:
                lines.append(
                    line
                )

        return "\n".join(
            lines
        )

    # ============================================================
    # SENTENCE SPLITTING
    # ============================================================

    @staticmethod
    def split_sentences(
        text: str,
    ) -> list[str]:
        """
        تقسيم النص إلى جمل.

        يدعم:
            .
            !
            ؟
            ?
            :
            ؛

        ونحاول الاحتفاظ بعلامة الترقيم
        مع الجملة السابقة.
        """

        if not text:
            return []

        pattern = re.compile(
            r"""
            .*?
            (?:
                [.!؟?؛]
                |
                :
                (?=\s|$)
                |
                \n
                |
                $
            )
            """,
            re.VERBOSE | re.DOTALL,
        )

        sentences = []

        for match in pattern.finditer(
            text
        ):

            sentence = (
                match.group(0)
                .strip()
            )

            if sentence:
                sentences.append(
                    sentence
                )

        return sentences

    # ============================================================
    # SPLIT VERY LONG SENTENCE
    # ============================================================

    def split_long_sentence(
        self,
        sentence: str,
    ) -> list[str]:
        """
        لو جملة واحدة أكبر من الحد،
        نحاول تقسيمها عند الفواصل.

        لو ما نفعش:
        نقسم عند الكلمات كحل أخير.
        """

        if (
            len(sentence)
            <= self.max_chars
        ):
            return [sentence]

        # --------------------------------------------------------
        # FIRST TRY:
        # split at Arabic/English commas
        # --------------------------------------------------------

        pieces = re.split(
            r"(?<=[،,])\s+",
            sentence,
        )

        output = []

        current = ""

        for piece in pieces:

            piece = piece.strip()

            if not piece:
                continue

            candidate = (
                f"{current} {piece}"
                if current
                else piece
            )

            if (
                len(candidate)
                <= self.max_chars
            ):

                current = candidate

            else:

                if current:
                    output.append(
                        current
                    )

                current = piece

        if current:
            output.append(
                current
            )

        # لو التقسيم نجح
        if all(
            len(item)
            <= self.max_chars
            for item in output
        ):
            return output

        # --------------------------------------------------------
        # LAST RESORT:
        # split by words
        # --------------------------------------------------------

        final_output = []

        for item in output:

            if (
                len(item)
                <= self.max_chars
            ):

                final_output.append(
                    item
                )

                continue

            words = item.split()

            current_words = []

            current_length = 0

            for word in words:

                extra_length = (
                    len(word)
                    + (
                        1
                        if current_words
                        else 0
                    )
                )

                if (
                    current_length
                    + extra_length
                    <= self.max_chars
                ):

                    current_words.append(
                        word
                    )

                    current_length += (
                        extra_length
                    )

                else:

                    if current_words:

                        final_output.append(
                            " ".join(
                                current_words
                            )
                        )

                    current_words = [
                        word
                    ]

                    current_length = (
                        len(word)
                    )

            if current_words:

                final_output.append(
                    " ".join(
                        current_words
                    )
                )

        return final_output

    # ============================================================
    # MAIN CHUNKING
    # ============================================================

    def chunk(
        self,
        text: str,
    ) -> list[TextChunk]:
        """
        تحويل النص الكامل إلى TextChunks.
        """

        text = self.clean_text(
            text
        )

        if not text:
            return []

        sentences = (
            self.split_sentences(
                text
            )
        )

        # معالجة الجمل الطويلة
        safe_sentences = []

        for sentence in sentences:

            safe_sentences.extend(
                self.split_long_sentence(
                    sentence
                )
            )

        chunks_text = []

        current_chunk = ""

        for sentence in safe_sentences:

            sentence = (
                sentence.strip()
            )

            if not sentence:
                continue

            candidate = (
                f"{current_chunk}\n{sentence}"
                if current_chunk
                else sentence
            )

            if (
                len(candidate)
                <= self.max_chars
            ):

                current_chunk = (
                    candidate
                )

            else:

                if current_chunk:

                    chunks_text.append(
                        current_chunk
                    )

                current_chunk = (
                    sentence
                )

        if current_chunk:

            chunks_text.append(
                current_chunk
            )

        # ========================================================
        # BUILD OBJECTS
        # ========================================================

        chunks = []

        for index, chunk_text in enumerate(
            chunks_text,
            start=1,
        ):

            chunks.append(
                TextChunk(
                    index=index,
                    text=chunk_text,
                    char_count=len(
                        chunk_text
                    ),
                )
            )

        return chunks

    # ============================================================
    # DEBUG
    # ============================================================

    @staticmethod
    def print_summary(
        chunks: list[TextChunk],
    ) -> None:

        print()
        print(
            "=" * 60
        )

        print(
            "TTS TEXT CHUNKS"
        )

        print(
            "=" * 60
        )

        print(
            f"Total chunks: "
            f"{len(chunks)}"
        )

        for chunk in chunks:

            preview = (
                chunk.text
                .replace(
                    "\n",
                    " ",
                )
            )

            if len(preview) > 70:

                preview = (
                    preview[:70]
                    + "..."
                )

            print(
                f"[CHUNK "
                f"{chunk.index:02d}] "
                f"{chunk.char_count} chars | "
                f"{preview}"
            )

        print(
            "=" * 60
        )