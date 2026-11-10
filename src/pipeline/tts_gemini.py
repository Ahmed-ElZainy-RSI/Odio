"""Layer 05 (Gemini path) — synthesise a page with Gemini TTS.

Gemini's TTS API has no word-timestamp output at all: `audio_timestamp` in the
SDK is explicitly documented as "not supported in Gemini API", and the
response carries nothing beyond raw audio. Confirmed by inspecting a live
response's fields, not just the docs. So this module only synthesises;
alignment is a separate step (align_whisper.py) because the two engines that
would otherwise supply it (Hume) or replace it (Gemini) don't overlap.

Output is raw 16-bit PCM mono at 24 kHz, per Gemini's documented mime type
(audio/L16, rate=24000).
"""
from google import genai
from google.genai import types

from pipeline import ratelimit, usage

MODEL = "gemini-3.1-flash-tts-preview"
VOICE = "Kore"
SAMPLE_RATE = 24000
MAX_ATTEMPTS = 3


class NoAudioReturned(RuntimeError):
    """The model answered with no audio. Not a bug here: retried, then the page fails alone."""


def synthesize(client: genai.Client, text: str, voice: str = VOICE, style: str = ""):
    """Returns (pcm_bytes, duration_ms). `style` is how to read, sent ahead of the text.

    The preview model sometimes answers an ordinary request with an empty candidate (0 output
    tokens), and the same text then works, so an empty answer is tried again.
    """
    for _ in range(MAX_ATTEMPTS):
        ratelimit.acquire("gemini", MODEL)
        resp = client.models.generate_content(
            model=MODEL,
            contents=style + text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                ),
            ),
        )
        units = usage.gemini_units(resp)
        try:
            pcm = resp.candidates[0].content.parts[0].inline_data.data
        except (AttributeError, IndexError, TypeError):
            usage.record("gemini", MODEL, "tts", chars=len(text), **units)  # billed, no audio
            continue
        duration_ms = len(pcm) / (2 * SAMPLE_RATE) * 1000
        usage.record("gemini", MODEL, "tts", chars=len(text), audio_ms=int(duration_ms), **units)
        return pcm, duration_ms
    raise NoAudioReturned(f"no audio after {MAX_ATTEMPTS} attempts")
