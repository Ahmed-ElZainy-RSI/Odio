"""Text-to-speech adapters.

Two implementations:

* :class:`GeminiTts` -- the real, paid path. Wraps the model the legacy pipeline uses.
* :class:`SyntheticTts` -- a deterministic, free generator used by tests and local
  development. It emits a genuine, well-formed 24 kHz mono 16-bit WAV, so everything
  downstream (duration measurement, alignment, upload, content-type checks) runs against
  real audio rather than a mock.

Both return duration measured from the produced audio, never estimated from character
count.

The V1 callback carries exactly one `Audio` artifact per page, so these return exactly one
buffer -- the legacy pipeline synthesizes two narrators per page
(``walidapro/run_full_pipeline.py:1057-1130``), which doubles cost and has nowhere to go in
the contract.
"""

from __future__ import annotations

import io
import math
import struct
import wave

from ai_engine.adapters.ai.aligner_whisper import measure_wav_duration_ms
from ai_engine.domain.errors import PROGRAMMING_ERRORS, AiInferenceError, ModelUnavailableError
from ai_engine.domain.tokens import AudioFormat
from ai_engine.observability import usage
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container, matching the legacy output exactly."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


class SyntheticTts:
    """Deterministic, free TTS. Produces real WAV audio, not a placeholder.

    Duration is derived from the text so different pages get different lengths, which
    matters: a fixed-length stub would hide bugs in duration handling.
    """

    name = "synthetic"
    provider = "local"
    model_id = "synthetic-v1"

    def __init__(self, *, ms_per_char: float = 60.0, sample_rate: int = SAMPLE_RATE) -> None:
        # 60 ms/char is the ratio README.md:182 measures on the real corpus (0.06 s/char).
        self._ms_per_char = ms_per_char
        self._sample_rate = sample_rate

    def synthesize(self, text: str, *, voice: str | None = None) -> tuple[bytes, AudioFormat, int]:
        if not text.strip():
            raise AiInferenceError("cannot synthesize empty text")

        target_ms = max(200, int(len(text) * self._ms_per_char))
        frames = int(self._sample_rate * target_ms / 1000)
        # A quiet tone rather than silence: silence can be optimised away by tools and
        # tells you nothing about whether the audio path really works.
        pcm = bytearray()
        for index in range(frames):
            value = int(3000 * math.sin(2 * math.pi * 220 * index / self._sample_rate))
            pcm += struct.pack("<h", value)

        payload = wrap_pcm_as_wav(bytes(pcm), sample_rate=self._sample_rate)
        fmt = AudioFormat(sample_rate_hz=self._sample_rate)
        duration_ms = measure_wav_duration_ms(payload)
        log.info(
            "tts.synthesized",
            stage="tts",
            engine=self.name,
            duration_ms=duration_ms,
            size_in_bytes=len(payload),
        )
        return payload, fmt, duration_ms


class GeminiTts:
    """The real, paid Gemini TTS path.

    Wraps the same model the legacy pipeline uses. The client is injected so this class can
    be constructed without credentials and unit-tested without spending anything.
    """

    name = "gemini"
    provider = "gemini"

    def __init__(
        self,
        *,
        model_id: str = "gemini-3.1-flash-tts-preview",
        voice: str = "Kore",
        api_key: str | None = None,
        client: object | None = None,
        timeout_seconds: float = 180.0,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self.model_id = model_id
        self._voice = voice
        self._api_key = api_key
        self._client = client
        self._timeout = timeout_seconds
        self._sample_rate = sample_rate

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ModelUnavailableError("GEMINI_API_KEY is not configured")
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependency-group failure
            raise ModelUnavailableError(f"google-genai is not installed: {exc}") from exc
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    def synthesize(self, text: str, *, voice: str | None = None) -> tuple[bytes, AudioFormat, int]:
        if not text.strip():
            raise AiInferenceError("cannot synthesize empty text")
        client = self._ensure_client()

        try:
            pcm = self._request(client, text, voice or self._voice)
        except (AiInferenceError, ModelUnavailableError):
            raise
        except PROGRAMMING_ERRORS:
            raise  # our bug, not the provider's: surfaces as INTERNAL_ERROR (spec 007)
        except Exception as exc:
            # Retries are owned by one layer; this adapter reports and does not loop.
            raise AiInferenceError(f"TTS provider failed: {type(exc).__name__}") from exc

        if not pcm:
            raise AiInferenceError("TTS provider returned no audio")

        payload = wrap_pcm_as_wav(pcm, sample_rate=self._sample_rate)
        duration_ms = measure_wav_duration_ms(payload)
        self._check_duration_is_sane(text, duration_ms)
        log.info(
            "tts.synthesized",
            stage="tts",
            engine=self.name,
            model=self.model_id,
            duration_ms=duration_ms,
            size_in_bytes=len(payload),
        )
        return payload, AudioFormat(sample_rate_hz=self._sample_rate), duration_ms

    def _request(self, client: object, text: str, voice: str) -> bytes:
        """Issue the synthesis call and return raw PCM."""
        from google.genai import types  # type: ignore[import-not-found]

        response = client.models.generate_content(  # type: ignore[attr-defined]
            model=self.model_id,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
        usage.record(
            self.provider, self.model_id, "tts", chars=len(text), **usage.gemini_units(response)
        )
        return self._audio_bytes(response)

    @staticmethod
    def _audio_bytes(response: object) -> bytes:
        """Dig the PCM out of a response, checking the shape on the way.

        Indexing straight into it turned an empty provider response into a TypeError or
        IndexError -- provider data that would then look like a bug in our code.
        """
        candidates = getattr(response, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = getattr(content, "parts", None) or []
        inline = getattr(parts[0], "inline_data", None) if parts else None
        data = getattr(inline, "data", None)
        if not data:
            raise AiInferenceError("TTS provider response contained no audio part")
        return data

    @staticmethod
    def _check_duration_is_sane(text: str, duration_ms: int) -> None:
        """Reject degenerate output.

        This gate exists because it actually happened: the legacy code records a ~224-second
        result for ~50 words (``walidapro/ocr_verifier/tts/pipeline.py:493-495``). Shipping
        that would produce an audio file that plays for four minutes of noise and a timing
        artifact that is nonsense for every token.
        """
        words = max(1, len(text.split()))
        ms_per_word = duration_ms / words
        if ms_per_word > 4_000:
            raise AiInferenceError(
                f"TTS returned {duration_ms / 1000:.1f}s for {words} words, which failed "
                "the duration sanity check"
            )
        if duration_ms < 100:
            raise AiInferenceError(f"TTS returned only {duration_ms}ms of audio")
