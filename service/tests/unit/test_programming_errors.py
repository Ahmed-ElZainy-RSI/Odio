"""Spec 007: a bug in our own code is never reported as a provider failure.

The AI adapters wrap whatever their provider call raises into ``AiInferenceError`` so the
page fails with a typed, retryable reason. That wrapping must not swallow a
``NameError``/``AttributeError``/``TypeError`` raised by *our* code: reported as
"TTS provider failed: TypeError" it looks like an outage, is counted as a provider failure,
and sends people to the wrong place. Those propagate to the page task, which reports
``INTERNAL_ERROR`` instead.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from ai_engine.adapters.ai.aligner_whisper import WhisperAligner
from ai_engine.adapters.ai.ocr_providers import GeminiVisionOcr
from ai_engine.adapters.ai.tts import GeminiTts
from ai_engine.domain.errors import PROGRAMMING_ERRORS, AiInferenceError
from ai_engine.observability.metrics import INFERENCE_FAILURES, Metrics
from tests.unit.test_ocr_providers import REPO_ROOT, StubGeminiClient


def test_the_programming_error_set() -> None:
    assert PROGRAMMING_ERRORS == (NameError, AttributeError, TypeError)


# ── OCR ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bug", [NameError("x"), AttributeError("x"), TypeError("x")])
def test_ocr_lets_a_programming_error_through(bug: Exception) -> None:
    metrics = Metrics()
    engine = GeminiVisionOcr(
        legacy_repo_root=REPO_ROOT, client=StubGeminiClient(raises=bug), metrics=metrics
    )
    with pytest.raises(type(bug)):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)
    assert (
        metrics.counter_value(
            INFERENCE_FAILURES,
            {"provider": "gemini", "model": "gemini-3.6-flash", "error_code": "AI_INFERENCE_ERROR"},
        )
        == 0
    ), "a bug must not be counted as a provider failure"


def test_ocr_still_wraps_a_real_provider_error() -> None:
    engine = GeminiVisionOcr(
        legacy_repo_root=REPO_ROOT, client=StubGeminiClient(raises=RuntimeError("503"))
    )
    with pytest.raises(AiInferenceError, match="OCR provider failed"):
        engine.extract_page(page_image=b"jpeg", pdf_path=None, page_number=1)


# ── TTS ─────────────────────────────────────────────────────────────────────────


class _Client:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.models = self
        self._response, self._raises = response, raises

    def generate_content(self, **kwargs: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture
def genai_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_request` builds a config from google.genai.types; any object will do here."""
    stub = types.SimpleNamespace(
        GenerateContentConfig=lambda **k: k,
        SpeechConfig=lambda **k: k,
        VoiceConfig=lambda **k: k,
        PrebuiltVoiceConfig=lambda **k: k,
    )
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.types = stub  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", stub)  # type: ignore[arg-type]


@pytest.mark.parametrize("bug", [NameError("x"), AttributeError("x"), TypeError("x")])
def test_tts_lets_a_programming_error_through(bug: Exception) -> None:
    class BuggyTts(GeminiTts):
        def _request(self, client: object, text: str, voice: str) -> bytes:
            raise bug

    with pytest.raises(type(bug)):
        BuggyTts(client=_Client()).synthesize("نص")


@pytest.mark.parametrize(
    "response",
    [
        types.SimpleNamespace(candidates=None),
        types.SimpleNamespace(candidates=[]),
        types.SimpleNamespace(candidates=[types.SimpleNamespace(content=None)]),
        types.SimpleNamespace(
            candidates=[types.SimpleNamespace(content=types.SimpleNamespace(parts=[]))]
        ),
    ],
    ids=["no-candidates", "empty-candidates", "no-content", "no-parts"],
)
def test_tts_degenerate_provider_response_is_a_provider_error(
    genai_types: None, response: Any
) -> None:
    """An empty response used to surface as TypeError/IndexError from indexing into it --
    provider data, not our bug -- so the shape is checked explicitly."""
    with pytest.raises(AiInferenceError, match="no audio"):
        GeminiTts(client=_Client(response=response)).synthesize("نص")


def test_tts_still_wraps_a_real_provider_error(genai_types: None) -> None:
    with pytest.raises(AiInferenceError, match="TTS provider failed: RuntimeError"):
        GeminiTts(client=_Client(raises=RuntimeError("500"))).synthesize("نص")


# ── Alignment ───────────────────────────────────────────────────────────────────


def _legacy_aligner(monkeypatch: pytest.MonkeyPatch, raises: Exception) -> WhisperAligner:
    module = types.ModuleType("pipeline.align_whisper")

    def align_words(path: str) -> list[dict]:
        raise raises

    module.align_words = align_words  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pipeline.align_whisper", module)
    return WhisperAligner(legacy_repo_root=Path(REPO_ROOT))


def test_aligner_lets_a_programming_error_through(monkeypatch: pytest.MonkeyPatch) -> None:
    aligner = _legacy_aligner(monkeypatch, TypeError("bug"))
    with pytest.raises(TypeError):
        aligner._transcribe("audio.wav")


def test_aligner_still_wraps_a_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    aligner = _legacy_aligner(monkeypatch, RuntimeError("ctranslate2 failed"))
    with pytest.raises(AiInferenceError, match="forced alignment failed"):
        aligner._transcribe("audio.wav")
