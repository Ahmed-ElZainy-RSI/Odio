"""Layer 05b — forced alignment for TTS engines with no native word timing.

Gemini's TTS returns audio only (see tts_gemini.py), so instead of trusting
the engine for timing, this re-listens to what it produced: faster-whisper
transcribes the generated audio and returns its own word timestamps. Measured
on a real page, 35 of 35 words came back in the correct order with clean
monotonic timings, comparable in quality to Hume's native output.

The output is shaped identically to Hume's word list -- [{"text","b","e"}] in
ms -- so it plugs into speak.py:bind() unchanged. bind() only cares that it
gets ordered (text, begin, end) triples; it doesn't care whether they came
from the TTS engine itself or from re-transcribing its output.
"""
from faster_whisper import WhisperModel

_model = None


def _get_model(size: str = "small") -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(size, device="cpu", compute_type="int8")
    return _model


def align_words(wav_path, language: str = "ar"):
    """Transcribe wav_path and return [{"text","b","e"}] word timings in ms."""
    segments, _ = _get_model().transcribe(str(wav_path), language=language, word_timestamps=True)
    return [
        {"text": w.word.strip(), "b": int(w.start * 1000), "e": int(w.end * 1000)}
        for seg in segments
        for w in seg.words
    ]
