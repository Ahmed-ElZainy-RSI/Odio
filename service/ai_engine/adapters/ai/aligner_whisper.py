"""Word-level alignment: forced transcription plus binding onto stable token ids.

This is the only path in the repository that can produce the V1 `JSON` timing artifact --
a repo-wide search for ``timing|timestamp|word_time|srt|vtt`` across ``walidapro/**/*.py``
returns zero hits.

Two pieces, wrapped rather than rewritten:

* ``pipeline.align_whisper.align_words`` re-transcribes the audio we just generated to
  recover word timestamps. Gemini TTS has no timestamp API at all, so for that engine this
  is the only option.
* ``pipeline.speak.bind`` maps those timestamps onto token ids. It is reused **verbatim**:
  115 lines, pure, dependency-free, engine-agnostic, already proven against two different
  timestamp sources, and already exercised by a second caller with a different token shape
  (``tools/build_review.py:105``). Its defensive details -- dropping inverted vendor
  intervals, a 6-token greedy match window, clamping interpolated starts so overlapping
  speech cannot invert a span -- are hard-won and should not be reimplemented.

Transcription is injected, so the aligner is testable without loading a 464 MB model, and
the real ``bind`` is exercised either way.

A quality note that belongs in the code rather than only in a document: across 95 real
pages, 78.0% of tokens anchor to a measured timestamp and 21.8% are interpolated -- and per
book that ranges from 51% (Gemini TTS + this path) to 94% (Hume native timestamps). The
`BindReport` carries that ratio so it is visible per page instead of being averaged away.
"""

from __future__ import annotations

import tempfile
import wave
from collections.abc import Callable, Sequence
from pathlib import Path

from ai_engine.adapters.ai.legacy import LegacyPipelineUnavailable, ensure_legacy_on_path
from ai_engine.domain.enums import TimingSource
from ai_engine.domain.errors import PROGRAMMING_ERRORS, AiInferenceError, ModelUnavailableError
from ai_engine.domain.tokens import AudioFormat, BindReport, TimedToken, Token
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)

#: A transcriber turns an audio file path into `[{"text","b","e"}]` word stamps in ms.
Transcriber = Callable[[str], list[dict]]


class WhisperAligner:
    """Implements :class:`~ai_engine.ports.ai.Aligner`."""

    def __init__(
        self,
        *,
        legacy_repo_root: Path | None,
        transcriber: Transcriber | None = None,
        method: str = "forced-whisper-small",
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._legacy_repo_root = legacy_repo_root
        self._transcriber = transcriber
        self.method = method
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._bind = None

    # ── Collaborator loading ──────────────────────────────────────────────────────

    def _load_bind(self):
        if self._bind is None:
            try:
                ensure_legacy_on_path(self._legacy_repo_root)
                from pipeline.speak import bind  # type: ignore[import-not-found]
            except (LegacyPipelineUnavailable, ImportError) as exc:
                raise ModelUnavailableError(f"the token binder could not be loaded: {exc}") from exc
            self._bind = bind
        return self._bind

    def _transcribe(self, audio_path: str) -> list[dict]:
        if self._transcriber is not None:
            return self._transcriber(audio_path)
        try:
            ensure_legacy_on_path(self._legacy_repo_root)
            from pipeline.align_whisper import align_words  # type: ignore[import-not-found]
        except (LegacyPipelineUnavailable, ImportError) as exc:
            raise ModelUnavailableError(f"the alignment model could not be loaded: {exc}") from exc
        try:
            return align_words(audio_path)
        except PROGRAMMING_ERRORS:
            raise  # a bug, not a model failure (spec 007)
        except Exception as exc:
            raise AiInferenceError(f"forced alignment failed: {type(exc).__name__}") from exc

    # ── The port ──────────────────────────────────────────────────────────────────

    def align(
        self,
        *,
        audio: bytes,
        audio_format: AudioFormat,
        duration_ms: int,
        tokens: Sequence[Token],
    ) -> tuple[Sequence[TimedToken], BindReport]:
        if not tokens:
            # An empty page is completable, not an error.
            return [], BindReport(tokens=0, anchored=0, interpolated=0, untimed=0, monotonic=True)

        bind = self._load_bind()

        with tempfile.TemporaryDirectory(prefix="ai-engine-align-") as scratch:
            audio_path = Path(scratch) / f"page.{audio_format.container}"
            audio_path.write_bytes(audio)
            stamps = self._transcribe(str(audio_path))

        # `bind` mutates the dicts it is given, so hand it throwaway copies.
        records = [{"txt": token.text} for token in tokens]
        bound, report = bind(records, stamps, duration_ms)

        timed: list[TimedToken] = []
        for token, record in zip(tokens, bound, strict=True):
            window = record.get("t")
            if window is None:
                timed.append(TimedToken.untimed(token.id, token.text))
                continue
            timed.append(
                TimedToken(
                    id=token.id,
                    text=token.text,
                    start_ms=int(window[0]),
                    end_ms=int(window[1]),
                    source=TimingSource(record["t_src"]),
                )
            )

        bind_report = BindReport(
            tokens=int(report["tokens"]),
            anchored=int(report["anchored"]),
            interpolated=int(report["interpolated"]),
            untimed=int(report["untimed"]),
            monotonic=bool(report["monotonic"]),
        )
        log.info(
            "alignment.completed",
            stage="align",
            method=self.method,
            tokens=bind_report.tokens,
            anchored=bind_report.anchored,
            interpolated=bind_report.interpolated,
            untimed=bind_report.untimed,
            anchor_ratio=round(bind_report.anchor_ratio, 4),
            monotonic=bind_report.monotonic,
        )
        return timed, bind_report


def measure_wav_duration_ms(payload: bytes) -> int:
    """Read a WAV's true duration from its header.

    Measured, never estimated from character count -- the legacy TTS code gets this right
    and it must stay right, because a wrong duration silently corrupts every timing in the
    artifact.
    """
    import io

    with wave.open(io.BytesIO(payload), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    if rate <= 0:
        raise AiInferenceError("audio declares a non-positive sample rate")
    return round(1000 * frames / rate)
