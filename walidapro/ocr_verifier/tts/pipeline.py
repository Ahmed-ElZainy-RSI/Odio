from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .audio_manager import AudioManager
from .gemini_tts import GeminiTTS
from .models import TTSChunkResult, TTSResult
from .text_chunker import TextChunker


class TTSPipeline:
    """
    Gemini TTS Pipeline for Arabic children's books.

    يدعم:
        - Female narrator
        - Male narrator

    Flow:

        Final Arabic Text
                ↓
        TextChunker
                ↓
        GeminiTTS
                ↓
        chunk_0001.wav
        chunk_0002.wav
        ...
                ↓
        AudioManager
                ↓
        final_audio.wav
                ↓
        report.json
    """

    def __init__(
        self,
        output_dir: str | Path = "tts_output",
        max_chars: int = 700,
        voice: str | None = None,
        narrator_type: str = "female",
    ) -> None:

        # ========================================================
        # OUTPUT
        # ========================================================

        self.output_dir = Path(
            output_dir
        )

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ========================================================
        # TTS ENGINE
        # ========================================================

        self.tts = GeminiTTS(
            voice=voice,
            narrator_type=narrator_type,
        )

        # ========================================================
        # NARRATOR INFO
        # ========================================================

        self.narrator_type = (
            narrator_type
            .strip()
            .lower()
        )

        # ========================================================
        # TEXT CHUNKER
        # ========================================================

        self.chunker = TextChunker(
            max_chars=max_chars
        )

        # ========================================================
        # AUDIO MANAGER
        # ========================================================

        self.audio_manager = AudioManager(
            output_dir=self.output_dir
        )

    # ============================================================
    # SAVE TEXT
    # ============================================================

    def _save_input_text(
        self,
        text: str,
    ) -> Path:

        path = (
            self.output_dir
            / "input_text.txt"
        )

        path.write_text(
            text,
            encoding="utf-8",
        )

        return path

    # ============================================================
    # SAVE REPORT
    # ============================================================

    def _save_report(
        self,
        result: TTSResult,
    ) -> Path:

        report_path = (
            self.output_dir
            / "report.json"
        )

        report_data = (
            result.to_dict()
        )

        # نضيف نوع الراوي للتقرير
        report_data[
            "narrator_type"
        ] = self.narrator_type

        report_path.write_text(
            json.dumps(
                report_data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return report_path

    # ============================================================
    # TTS CACHE / QUALITY
    # ============================================================

    @staticmethod
    def _audio_cache_meta_path(
        audio_path: Path,
    ) -> Path:
        return audio_path.with_suffix(
            ".meta.json"
        )

    def _chunk_fingerprint(
        self,
        text: str,
    ) -> str:
        payload = "|".join(
            [
                "tts-cache-v1-20260820",
                self.tts.model,
                self.tts.voice,
                self.narrator_type,
                text,
            ]
        )
        return hashlib.sha256(
            payload.encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def _arabic_word_count(
        text: str,
    ) -> int:
        return len(
            re.findall(
                r"[\u0621-\u063A\u0641-\u064A\u0671-\u06D3]+",
                text or "",
            )
        )

    @classmethod
    def _duration_is_sane(
        cls,
        text: str,
        duration: float,
    ) -> bool:
        words = max(
            1,
            cls._arabic_word_count(
                text
            ),
        )

        # Intentionally wide children's-story bounds.
        # Page-10-like 224s for ~50 words is rejected, while slow narration
        # remains allowed.
        min_duration = max(
            0.5,
            words * 0.10,
        )
        max_duration = max(
            12.0,
            words * 2.50 + 8.0,
        )

        return (
            min_duration
            <= duration
            <= max_duration
        )

    def _load_cached_chunk(
        self,
        text: str,
        audio_path: Path,
    ) -> tuple[Path, float] | None:
        meta_path = self._audio_cache_meta_path(
            audio_path
        )
        if (
            not audio_path.exists()
            or not meta_path.exists()
        ):
            return None
        try:
            meta = json.loads(
                meta_path.read_text(
                    encoding="utf-8"
                )
            )
            if meta.get("fingerprint") != self._chunk_fingerprint(text):
                return None
            duration = self.audio_manager.duration_seconds(
                audio_path
            )
            if not self._duration_is_sane(
                text,
                duration,
            ):
                return None
            return audio_path, duration
        except Exception:
            return None

    def _save_chunk_cache_meta(
        self,
        text: str,
        audio_path: Path,
        duration: float,
    ) -> None:
        meta_path = self._audio_cache_meta_path(
            audio_path
        )
        meta_path.write_text(
            json.dumps(
                {
                    "fingerprint": self._chunk_fingerprint(text),
                    "duration_seconds": duration,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ============================================================
    # RUN
    # ============================================================

    def run(
        self,
        text: str,
        input_name: str = "page",
    ) -> TTSResult:
        """
        تحويل نص عربي كامل إلى صوت.

        Parameters
        ----------
        text:
            النص النهائي الذي نريد قراءته.

        input_name:
            اسم الصفحة أو الكتاب.
        """

        # ========================================================
        # VALIDATE TEXT
        # ========================================================

        if not text:

            raise ValueError(
                "TTS input text is empty."
            )

        text = text.strip()

        if not text:

            raise ValueError(
                "TTS input text contains only whitespace."
            )

        # ========================================================
        # HEADER
        # ========================================================

        print()
        print(
            "=" * 70
        )

        print(
            "ARABIC CHILDREN BOOK TTS"
        )

        print(
            "=" * 70
        )

        print(
            f"[INPUT] "
            f"{input_name}"
        )

        print(
            f"[MODEL] "
            f"{self.tts.model}"
        )

        print(
            f"[VOICE] "
            f"{self.tts.voice}"
        )

        print(
            f"[NARRATOR] "
            f"{self.narrator_type}"
        )

        print()

        # ========================================================
        # SAVE ORIGINAL INPUT
        # ========================================================

        input_text_path = (
            self._save_input_text(
                text
            )
        )

        print(
            f"[TEXT] Saved: "
            f"{input_text_path}"
        )

        # ========================================================
        # CHUNK TEXT
        # ========================================================

        chunks = (
            self.chunker.chunk(
                text
            )
        )

        if not chunks:

            raise RuntimeError(
                "TextChunker returned no chunks."
            )

        print(
            f"[CHUNKER] "
            f"{len(chunks)} "
            f"chunks created"
        )

        print()

        # ========================================================
        # RESULT OBJECT
        # ========================================================

        result = TTSResult(
            input_name=input_name,
            model=self.tts.model,
            voice=self.tts.voice,
        )

        generated_audio_files: list[
            Path
        ] = []

        # ========================================================
        # GENERATE CHUNKS
        # ========================================================

        for chunk in chunks:

            print(
                "=" * 70
            )

            print(
                f"CHUNK "
                f"{chunk.index}/"
                f"{len(chunks)}"
            )

            print(
                "=" * 70
            )

            print(
                f"[CHARS] "
                f"{chunk.char_count}"
            )

            print()

            print(
                "[TEXT]"
            )

            print(
                chunk.text
            )

            print()

            audio_path = (
                self.audio_manager
                .chunk_path(
                    chunk.index
                )
            )

            # ====================================================
            # GEMINI TTS
            # ====================================================

            try:

                cached_chunk = self._load_cached_chunk(
                    chunk.text,
                    audio_path,
                )

                if cached_chunk is not None:
                    generated_path, duration = (
                        cached_chunk
                    )
                    print(
                        f"[TTS CACHE] "
                        f"Chunk {chunk.index} reused"
                    )
                else:
                    generated_path = (
                        self.tts.generate(
                            text=chunk.text,
                            output_path=audio_path,
                            retries=2,
                        )
                    )

                    duration = (
                        self.audio_manager
                        .duration_seconds(
                            generated_path
                        )
                    )

                    if not self._duration_is_sane(
                        chunk.text,
                        duration,
                    ):
                        # One controlled regeneration for anomalous audio.
                        # This specifically catches extreme silence/repetition
                        # such as the previously observed ~224s page.
                        print(
                            f"[TTS QUALITY] "
                            f"Abnormal duration {duration:.2f}s; "
                            "regenerating once"
                        )

                        try:
                            generated_path.unlink(
                                missing_ok=True
                            )
                        except Exception:
                            pass

                        generated_path = (
                            self.tts.generate(
                                text=chunk.text,
                                output_path=audio_path,
                                retries=1,
                            )
                        )
                        duration = (
                            self.audio_manager
                            .duration_seconds(
                                generated_path
                            )
                        )

                        if not self._duration_is_sane(
                            chunk.text,
                            duration,
                        ):
                            raise RuntimeError(
                                "TTS duration failed quality gate after one regeneration: "
                                f"{duration:.2f}s"
                            )

                    self._save_chunk_cache_meta(
                        chunk.text,
                        generated_path,
                        duration,
                    )

                generated_audio_files.append(
                    generated_path
                )

                chunk_result = (
                    TTSChunkResult(
                        index=chunk.index,

                        text=chunk.text,

                        audio_path=str(
                            generated_path
                        ),

                        char_count=(
                            chunk.char_count
                        ),

                        duration_seconds=(
                            duration
                        ),

                        status="OK",

                        error=None,
                    )
                )

                print(
                    f"[SUCCESS] "
                    f"Chunk "
                    f"{chunk.index}"
                )

                print(
                    f"[DURATION] "
                    f"{duration:.2f}s"
                )

            except Exception as exc:

                print(
                    f"[ERROR] "
                    f"Chunk "
                    f"{chunk.index}: "
                    f"{exc}"
                )

                chunk_result = (
                    TTSChunkResult(
                        index=chunk.index,

                        text=chunk.text,

                        audio_path=str(
                            audio_path
                        ),

                        char_count=(
                            chunk.char_count
                        ),

                        duration_seconds=None,

                        status="ERROR",

                        error=str(
                            exc
                        ),
                    )
                )

            result.add_chunk(
                chunk_result
            )

            print()

        # ========================================================
        # SAFETY CHECK
        # ========================================================

        if result.failed_chunks > 0:

            print()

            print(
                "[WARNING] "
                f"{result.failed_chunks} "
                "TTS chunks failed."
            )

            print(
                "[WARNING] "
                "Final audio will NOT be merged "
                "because the page/book would be incomplete."
            )

            result.final_audio_path = None

            report_path = (
                self._save_report(
                    result
                )
            )

            print(
                f"[REPORT] "
                f"{report_path}"
            )

            return result

        # ========================================================
        # MERGE AUDIO
        # ========================================================

        final_audio_path = (
            self.output_dir
            / "final_audio.wav"
        )

        self.audio_manager.merge_wav_files(
            files=generated_audio_files,
            output_path=final_audio_path,
        )

        result.final_audio_path = str(
            final_audio_path
        )

        # ========================================================
        # FINAL DURATION
        # ========================================================

        result.total_duration_seconds = (
            self.audio_manager
            .duration_seconds(
                final_audio_path
            )
        )

        # ========================================================
        # REPORT
        # ========================================================

        report_path = (
            self._save_report(
                result
            )
        )

        # ========================================================
        # SUMMARY
        # ========================================================

        print()
        print(
            "=" * 70
        )

        print(
            "TTS COMPLETED"
        )

        print(
            "=" * 70
        )

        print(
            f"Narrator    : "
            f"{self.narrator_type}"
        )

        print(
            f"Voice       : "
            f"{result.voice}"
        )

        print(
            f"Model       : "
            f"{result.model}"
        )

        print(
            f"Chunks      : "
            f"{result.total_chunks}"
        )

        print(
            f"Successful  : "
            f"{result.successful_chunks}"
        )

        print(
            f"Failed      : "
            f"{result.failed_chunks}"
        )

        print(
            f"Characters  : "
            f"{result.total_characters}"
        )

        print(
            f"Duration    : "
            f"{result.total_duration_seconds:.2f}s"
        )

        print(
            f"Final audio : "
            f"{final_audio_path}"
        )

        print(
            f"Report      : "
            f"{report_path}"
        )

        print(
            "=" * 70
        )

        return result