"""Token and timing value objects.

These mirror what the existing pipeline already produces on disk -- see the real records
in `work/<book>/pNNN.json` -- but as an explicit, typed contract rather than loose dicts.

Field naming follows the existing artifacts (`txt`, `t`, `t_src`) where those values cross
a boundary, because 186 real page records already use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from ai_engine.domain.enums import TimingSource

#: Token ids embed the page number, so a page's tokens cannot be renumbered without
#: invalidating every downstream reference (boxes, timings, review rows).
#: src/pipeline/tashkel.py:19-22 calls this out as load-bearing.
TOKEN_ID_TEMPLATE = "p{page:03d}w{index:03d}"


def token_id(page: int, index: int) -> str:
    """Build a stable token id.

    Pure function of ``(page, index)``, which is what makes Job B able to reproduce Job
    A's ids from the text artifact alone.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index}")
    return TOKEN_ID_TEMPLATE.format(page=page, index=index)


@dataclass(frozen=True, slots=True)
class Token:
    """One word of a page.

    `text` is the reading text -- corrected and diacritized -- corresponding to `txt` in
    the existing page records.
    """

    id: str
    text: str
    raw: str | None = None
    box: tuple[float, float, float, float] | None = None
    fixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("token id must not be empty")
        # The Text artifact is a space-join of these values, so a token containing
        # whitespace would make the join non-invertible and break Job B's re-tokenization.
        if self.text != self.text.strip() or any(c.isspace() for c in self.text):
            raise ValueError(f"token text must not contain whitespace: {self.text!r}")


@dataclass(frozen=True, slots=True)
class TimedToken:
    """A token with a resolved playback window.

    ``start_ms``/``end_ms`` are both None when no timing could be established for this
    token. That is a legitimate, expected state -- 7 of 3,873 real tokens are in it -- and
    a reader shows such a word unhighlighted rather than guessing.
    """

    id: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    source: TimingSource | None = None

    def __post_init__(self) -> None:
        has_window = self.start_ms is not None and self.end_ms is not None
        if has_window:
            if self.start_ms < 0:  # type: ignore[operator]
                raise ValueError(f"start_ms must be >= 0, got {self.start_ms}")
            if self.end_ms < self.start_ms:  # type: ignore[operator]
                raise ValueError(
                    f"end_ms {self.end_ms} precedes start_ms {self.start_ms} for {self.id}"
                )
            if self.source is None:
                raise ValueError(f"a timed token must declare its source: {self.id}")
        else:
            if self.start_ms is not None or self.end_ms is not None:
                raise ValueError(f"half-open timing window on {self.id}")
            if self.source is not None:
                raise ValueError(f"untimed token must not declare a source: {self.id}")

    @property
    def is_timed(self) -> bool:
        return self.start_ms is not None

    @classmethod
    def untimed(cls, id: str, text: str) -> Self:
        return cls(id=id, text=text)


@dataclass(frozen=True, slots=True)
class BindReport:
    """Quality record for one page's alignment.

    The existing pipeline already computes this (src/pipeline/speak.py:99) and writes it
    to disk, where nothing has ever aggregated it. It is the single best quality signal in
    the system, so it becomes first-class here.
    """

    tokens: int
    anchored: int
    interpolated: int
    untimed: int
    monotonic: bool

    def __post_init__(self) -> None:
        counted = self.anchored + self.interpolated + self.untimed
        if counted != self.tokens:
            raise ValueError(
                f"bind report does not add up: {self.anchored}+{self.interpolated}"
                f"+{self.untimed} != {self.tokens}"
            )

    @property
    def anchor_ratio(self) -> float:
        """Fraction of tokens bound to a real speech timestamp.

        Corpus baseline measured over 95 real pages is 0.78; per book the observed range
        is 0.51 (Gemini TTS + forced alignment) to 0.94 (Hume native timestamps).
        """
        return self.anchored / self.tokens if self.tokens else 0.0

    @classmethod
    def from_tokens(cls, tokens: list[TimedToken]) -> Self:
        anchored = sum(1 for t in tokens if t.source is TimingSource.ANCHOR)
        interpolated = sum(1 for t in tokens if t.source is TimingSource.INTERPOLATED)
        untimed = sum(1 for t in tokens if not t.is_timed)
        return cls(
            tokens=len(tokens),
            anchored=anchored,
            interpolated=interpolated,
            untimed=untimed,
            monotonic=is_monotonic(tokens),
        )


def is_monotonic(tokens: list[TimedToken]) -> bool:
    """True when timed windows are non-decreasing and non-overlapping across the page."""
    previous_end: int | None = None
    for tok in tokens:
        if not tok.is_timed:
            continue
        if previous_end is not None and tok.start_ms < previous_end:  # type: ignore[operator]
            return False
        previous_end = tok.end_ms
    return True


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """Describes generated audio exactly, so a consumer can validate without decoding.

    Defaults match what the pipeline actually emits: 24 kHz mono 16-bit PCM in a WAV
    container (src/pipeline/run_speak_gemini.py:44-47).
    """

    codec: str = "pcm_s16le"
    container: str = "wav"
    sample_rate_hz: int = 24_000
    channels: int = 1
    bit_depth: int = 16

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.channels <= 0:
            raise ValueError("channels must be positive")


@dataclass(frozen=True, slots=True)
class PageProvenance:
    """Which models produced a page, and how well.

    Extends the convention already present in the page records, which carry
    `ocr_engine`, `tts`, `voice` and `align`.
    """

    ocr_engine: str | None = None
    diacritizer: str | None = None
    tts_model: str | None = None
    voice: str | None = None
    align_method: str | None = None
    box_level: str = "none"
    extra: dict[str, str] = field(default_factory=dict)
