from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TTSChunkResult:
    """
    نتيجة تحويل Text Chunk واحد إلى صوت.
    """

    index: int
    text: str

    audio_path: str

    char_count: int = 0

    duration_seconds: float | None = None

    status: str = "OK"

    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TTSResult:
    """
    نتيجة تشغيل TTS لنص كامل / صفحة / كتاب.
    """

    input_name: str

    model: str
    voice: str

    final_audio_path: str | None = None

    total_chunks: int = 0

    successful_chunks: int = 0

    failed_chunks: int = 0

    total_characters: int = 0

    total_duration_seconds: float = 0.0

    chunks: list[TTSChunkResult] | None = None

    def __post_init__(self) -> None:
        if self.chunks is None:
            self.chunks = []

    def add_chunk(
        self,
        chunk: TTSChunkResult,
    ) -> None:

        if self.chunks is None:
            self.chunks = []

        self.chunks.append(
            chunk
        )

        self.total_chunks += 1

        self.total_characters += (
            chunk.char_count
        )

        if chunk.status == "OK":
            self.successful_chunks += 1

        else:
            self.failed_chunks += 1

        if chunk.duration_seconds:
            self.total_duration_seconds += (
                chunk.duration_seconds
            )

    def to_dict(self) -> dict:
        return asdict(self)