"""Layers 05-06 for one book via Gemini TTS + forced alignment.

Gemini has no native word timestamps (see tts_gemini.py), so this binds
timings from align_whisper.py instead of from the engine response -- the
only difference from run_speak_hume.py's Hume path. bind() itself is
unchanged and engine-agnostic.

Resumable: a page whose audio already exists is skipped, so a rate limit or
quota ceiling costs at most one page on retry. Pages with zero tokens (e.g. a
genuinely image-only page) are skipped rather than sent to the API.
"""
import json
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from google import genai

from pipeline.speak import bind
from pipeline.tts_gemini import synthesize, VOICE, MODEL, SAMPLE_RATE
from pipeline.align_whisper import align_words
from pipeline.pages import parse_pages, in_range


def do_page(client, path, audio_dir, voice):
    rec = json.loads(path.read_text(encoding="utf-8"))
    wav = audio_dir / f"p{rec['page']:03d}.wav"

    if not rec["tokens"]:
        rec["status"] = "skipped-empty"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  p{rec['page']:03d}  0 tokens, skipped")
        return
    if wav.exists() and rec.get("status") == "spoken":
        print(f"  p{rec['page']:03d}  already done, skipping")
        return

    text = " ".join(t["txt"] for t in rec["tokens"])
    pcm, duration_ms = synthesize(client, text, voice)

    audio_dir.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)

    words = align_words(wav)
    tokens, report = bind(rec["tokens"], words, int(duration_ms))

    rec.update({"tokens": tokens, "tts": f"gemini:{MODEL}", "voice": voice,
                "whisper_words": words,   # persisted so re-binding needs no re-transcription
                "align": "forced-whisper-small",
                "duration_ms": int(duration_ms),
                "audio": f"audio/p{rec['page']:03d}.wav",
                "bind_report": report, "status": "spoken"})
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  p{rec['page']:03d}  {duration_ms/1000:>6.2f}s  "
          f"whisper {report['hume_timestamps']:>3} words -> {report['anchored']:>3} anchored + "
          f"{report['interpolated']:>3} interp = {report['tokens']:>3}  "
          f"{'mono' if report['monotonic'] else 'NON-MONOTONIC'}")


def run(work_dir: Path, voice: str = VOICE, pages="0"):
    """Synthesise pages in work_dir. pages: "0" = all, "5" = pages 1-5, "13:20" = pages 13-20."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    audio_dir = work_dir / "audio"
    start, end = parse_pages(pages)
    all_pages = sorted(work_dir.glob("p*.json"))
    page_files = [f for f in all_pages if in_range(int(f.stem[1:]), start, end)]
    print(f"{work_dir.name} — {len(page_files)} of {len(all_pages)} pages, "
          f"{MODEL} voice={voice!r} + faster-whisper alignment\n")
    for p in page_files:
        try:
            do_page(client, p, audio_dir, voice)
        except Exception as e:
            print(f"  {p.name}  FAILED: {type(e).__name__}: {str(e)[:150]}")
            print("  stopping — rerun to resume from here")
            return
    print("\ndone")


def main():
    book = os.environ.get("BOOK", "المومياوات ناشيونال جيوجرافيك-lighton")
    voice = os.environ.get("VOICE", VOICE)
    run(Path(f"work/{book}"), voice, pages=os.environ.get("PAGES", "0"))


if __name__ == "__main__":
    main()
