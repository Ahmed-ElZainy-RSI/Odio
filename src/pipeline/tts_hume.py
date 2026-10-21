"""Layer 05 (Hume path) — synthesise a page with Hume TTS.

Hume emits native word-level timestamps in the same call that produces audio,
unlike the Gemini path (tts_gemini.py + align_whisper.py need two steps and a
local model). But Hume doesn't emit exactly one timestamp per word either —
speak.py:bind() still does the anchor/interpolate work on its output.
"""
import base64

from pipeline import ratelimit, usage

from hume.tts import PostedUtterance, PostedUtteranceVoiceWithName

VOICE = "Layla, Arabic Philosopher"


async def synthesize(client, text: str, voice: str = VOICE):
    """Returns (mp3_bytes, words, duration_ms). words is Hume's raw word list."""
    ratelimit.acquire("hume", "octave-2")
    r = await client.tts.synthesize_json(
        utterances=[PostedUtterance(text=text,
            voice=PostedUtteranceVoiceWithName(name=voice, provider="HUME_AI"))],
        version="2", include_timestamp_types=["word"])
    g = r.generations[0]
    usage.record("hume", "octave-2", "tts", chars=len(text), audio_ms=int(g.duration * 1000))
    words = [{"text": t.text, "b": t.time.begin, "e": t.time.end}
             for s in [x for grp in (g.snippets or []) for x in grp]
             for t in (s.timestamps or [])]
    return base64.b64decode(g.audio), words, int(g.duration * 1000)
