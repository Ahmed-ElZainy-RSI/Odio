from __future__ import annotations

import wave
from pathlib import Path


class AudioManager:
    """
    إدارة ملفات الصوت الناتجة من Gemini TTS.

    المهام:
    - إنشاء فولدرات الصوت.
    - حفظ كل Chunk.
    - دمج ملفات WAV بالترتيب.
    - التحقق إن كل الملفات بنفس إعدادات الصوت.
    """

    def __init__(
        self,
        output_dir: Path,
    ) -> None:

        self.output_dir = Path(
            output_dir
        )

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.chunks_dir = (
            self.output_dir
            / "chunks"
        )

        self.chunks_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    # ============================================================
    # CHUNK PATH
    # ============================================================

    def chunk_path(
        self,
        index: int,
    ) -> Path:
        """
        مسار ملف Chunk واحد.
        """

        return (
            self.chunks_dir
            / f"chunk_{index:04d}.wav"
        )

    # ============================================================
    # VALIDATE WAV
    # ============================================================

    @staticmethod
    def _read_wav_info(
        path: Path,
    ) -> tuple[
        int,
        int,
        int,
        int,
        bytes,
    ]:
        """
        قراءة معلومات WAV.

        Returns:
            channels
            sample_width
            frame_rate
            frame_count
            pcm_data
        """

        with wave.open(
            str(path),
            "rb",
        ) as wav_file:

            channels = (
                wav_file.getnchannels()
            )

            sample_width = (
                wav_file.getsampwidth()
            )

            frame_rate = (
                wav_file.getframerate()
            )

            frame_count = (
                wav_file.getnframes()
            )

            pcm_data = (
                wav_file.readframes(
                    frame_count
                )
            )

        return (
            channels,
            sample_width,
            frame_rate,
            frame_count,
            pcm_data,
        )

    # ============================================================
    # MERGE WAV FILES
    # ============================================================

    def merge_wav_files(
        self,
        files: list[Path],
        output_path: Path,
    ) -> Path:
        """
        دمج ملفات WAV بالترتيب.

        كل الملفات لازم تكون بنفس:
        - channels
        - sample width
        - sample rate
        """

        if not files:
            raise ValueError(
                "No WAV files provided."
            )

        files = [
            Path(path)
            for path in files
        ]

        for path in files:

            if not path.exists():

                raise FileNotFoundError(
                    f"Audio chunk not found: {path}"
                )

        output_path = Path(
            output_path
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ========================================================
        # FIRST FILE SETTINGS
        # ========================================================

        (
            base_channels,
            base_sample_width,
            base_frame_rate,
            _,
            first_pcm,
        ) = self._read_wav_info(
            files[0]
        )

        pcm_parts: list[
            bytes
        ] = [
            first_pcm
        ]

        # ========================================================
        # REMAINING FILES
        # ========================================================

        for path in files[1:]:

            (
                channels,
                sample_width,
                frame_rate,
                _,
                pcm_data,
            ) = self._read_wav_info(
                path
            )

            if (
                channels
                != base_channels
            ):

                raise RuntimeError(
                    (
                        f"Channels mismatch in {path}. "
                        f"Expected {base_channels}, "
                        f"got {channels}."
                    )
                )

            if (
                sample_width
                != base_sample_width
            ):

                raise RuntimeError(
                    (
                        f"Sample width mismatch in {path}. "
                        f"Expected {base_sample_width}, "
                        f"got {sample_width}."
                    )
                )

            if (
                frame_rate
                != base_frame_rate
            ):

                raise RuntimeError(
                    (
                        f"Sample rate mismatch in {path}. "
                        f"Expected {base_frame_rate}, "
                        f"got {frame_rate}."
                    )
                )

            pcm_parts.append(
                pcm_data
            )

        # ========================================================
        # WRITE FINAL WAV
        # ========================================================

        with wave.open(
            str(output_path),
            "wb",
        ) as output_wav:

            output_wav.setnchannels(
                base_channels
            )

            output_wav.setsampwidth(
                base_sample_width
            )

            output_wav.setframerate(
                base_frame_rate
            )

            for pcm_data in pcm_parts:

                output_wav.writeframes(
                    pcm_data
                )

        print(
            f"[AUDIO] Merged "
            f"{len(files)} chunks"
        )

        print(
            f"[AUDIO] Final file: "
            f"{output_path}"
        )

        return output_path

    # ============================================================
    # AUDIO DURATION
    # ============================================================

    @staticmethod
    def duration_seconds(
        path: Path,
    ) -> float:
        """
        مدة ملف WAV بالثواني.
        """

        path = Path(
            path
        )

        with wave.open(
            str(path),
            "rb",
        ) as wav_file:

            frames = (
                wav_file.getnframes()
            )

            frame_rate = (
                wav_file.getframerate()
            )

        if frame_rate <= 0:
            return 0.0

        return (
            frames
            / float(frame_rate)
        )

    # ============================================================
    # PRINT SUMMARY
    # ============================================================

    def print_summary(
        self,
        files: list[Path],
        final_file: Path | None = None,
    ) -> None:

        print()
        print(
            "=" * 60
        )

        print(
            "AUDIO SUMMARY"
        )

        print(
            "=" * 60
        )

        total_duration = 0.0

        for index, path in enumerate(
            files,
            start=1,
        ):

            duration = (
                self.duration_seconds(
                    path
                )
            )

            total_duration += (
                duration
            )

            print(
                f"Chunk {index:02d} : "
                f"{duration:.2f}s "
                f"-> {path}"
            )

        print(
            "-"
            * 60
        )

        print(
            f"Total duration : "
            f"{total_duration:.2f}s"
        )

        if final_file is not None:

            print(
                f"Final audio    : "
                f"{final_file}"
            )

        print(
            "=" * 60
        )