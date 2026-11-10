"""Layers 05-06 for a book read by a cast: a narrator, and a voice for each character.

`cast.json` in the book's directory says who reads what:

    {"roles": {"narrator": {"voice": "Puck", "style": "..."}, "mother": {...}},
     "lines": {"5": [[5, 20, "boy"]]}}

`lines` maps a page to [first token, token after the last, role]; everything else on the page
is the narrator's. Each stretch is one request. Its words are timed on its own audio and then
moved along by where it lands on the page, so a misheard word can't disturb the next speaker.

Resumable the same way as run_speak_gemini.py: a spoken page with audio is skipped.
"""
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from google import genai

from pipeline import failures, usage
from pipeline.align_whisper import align_words
from pipeline.records import write_record
from pipeline.speak import bind, key
from pipeline.tts_gemini import MODEL, NoAudioReturned, SAMPLE_RATE, synthesize

NARRATOR = "narrator"
PAUSE_MS = 250  # between speakers
MAX_ATTEMPTS = 2


def stretches(n_tokens: int, lines: list) -> list[tuple[int, int, str]]:
    """The whole page as (first, after last, role): the given lines, with the narrator between."""
    out, at = [], 0
    for first, last, role in sorted(lines):
        if first < at or last > n_tokens or first >= last:
            raise ValueError(f"line [{first}, {last}] of {role!r} doesn't fit {n_tokens} tokens")
        if first > at:
            out.append((at, first, NARRATOR))
        out.append((first, last, role))
        at = last
    if at < n_tokens:
        out.append((at, n_tokens, NARRATOR))
    return out


def _read_instructions_aloud(words: list, tokens: list) -> bool:
    """Far more words than the line has: the model spoke its style instructions too."""
    return len(words) > 2 * len(tokens) + 4


def _time_stretch(tokens: list, words: list, duration_ms: float, offset_ms: int) -> dict:
    """Time one stretch's tokens, in page time. Nothing recognised still shares out its duration."""
    tokens, report = bind(tokens, words, int(duration_ms))
    untimed = [t for t in tokens if "t" not in t]
    if untimed:
        share = duration_ms / sum(max(1, len(key(t["txt"]))) for t in untimed)
        at = 0.0
        for t in untimed:
            step = share * max(1, len(key(t["txt"])))
            t["t"], t["t_src"] = [int(at), int(at + step)], "interpolated"
            at += step
        report["interpolated"] += len(untimed)
    for t in tokens:
        t["t"] = [t["t"][0] + offset_ms, t["t"][1] + offset_ms]
    return report


def _speak(client, tokens: list, role: dict) -> tuple[bytes, float, list]:
    text = " ".join(t["txt"] for t in tokens)
    for _ in range(MAX_ATTEMPTS):
        pcm, duration_ms = synthesize(client, text, role["voice"], role.get("style", ""))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stretch.wav"
            _write_wav(wav, pcm)
            words = align_words(wav)
        if not _read_instructions_aloud(words, tokens):
            return pcm, duration_ms, words
    raise NoAudioReturned(f"heard {len(words)} words for a {len(tokens)}-word line")


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def do_page(client, path: Path, audio_dir: Path, cast: dict) -> None:
    rec = json.loads(path.read_text(encoding="utf-8"))
    wav = audio_dir / f"p{rec['page']:03d}.wav"

    if not rec["tokens"]:
        rec["status"] = "skipped-empty"
        write_record(path, rec)
        return
    if wav.exists() and rec.get("status") == "spoken":
        print(f"  p{rec['page']:03d}  already done, skipping")
        return

    pause = b"\0\0" * (SAMPLE_RATE * PAUSE_MS // 1000)
    parts = stretches(len(rec["tokens"]), cast["lines"].get(str(rec["page"]), []))
    audio, heard, offset_ms = [], [], 0
    total = {"hume_timestamps": 0, "hume_dropped_invalid": 0, "tokens": 0,
             "anchored": 0, "interpolated": 0, "untimed": 0}
    for i, (first, last, role) in enumerate(parts):
        tokens = rec["tokens"][first:last]
        pcm, duration_ms, words = _speak(client, tokens, cast["roles"][role])
        report = _time_stretch(tokens, words, duration_ms, offset_ms)
        for name in total:
            total[name] += report[name]
        heard += [{**w, "b": w["b"] + offset_ms, "e": w["e"] + offset_ms} for w in words]
        audio.append(pcm)
        offset_ms += int(duration_ms)
        if i < len(parts) - 1:
            audio.append(pause)
            offset_ms += PAUSE_MS

    times = [t["t"] for t in rec["tokens"]]
    total["monotonic"] = all(a[0] <= b[0] for a, b in zip(times, times[1:]))
    audio_dir.mkdir(parents=True, exist_ok=True)
    _write_wav(wav, b"".join(audio))
    rec.update({"tts": f"gemini:{MODEL}", "voice": "cast",
                "cast": [[first, last, role, cast["roles"][role]["voice"]] for first, last, role in parts],
                "whisper_words": heard, "align": "forced-whisper-small", "duration_ms": offset_ms,
                "audio": f"audio/p{rec['page']:03d}.wav", "bind_report": total, "status": "spoken"})
    write_record(path, rec)
    print(f"  p{rec['page']:03d}  {offset_ms / 1000:>6.2f}s  {len(parts)} parts  "
          f"{total['anchored']:>3} anchored + {total['interpolated']:>3} interp = {total['tokens']:>3}  "
          f"{'mono' if total['monotonic'] else 'NON-MONOTONIC'}")


def _run(work_dir: Path, pages: set[int] | None = None) -> dict:
    cast = json.loads((work_dir / "cast.json").read_text(encoding="utf-8"))
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    summary = {"done": 0, "failed": 0, "stopped_on_quota": False}
    for p in sorted(work_dir.glob("p*.json")):
        page = int(p.stem[1:])
        if pages and page not in pages:
            continue
        try:
            with usage.page(page):
                do_page(client, p, work_dir / "audio", cast)
        except Exception as e:
            if failures.handle(work_dir, page, "tts", e, f"  {p.name}") == "quota":
                summary["stopped_on_quota"] = True
                break
            summary["failed"] += 1
            continue
        failures.clear(work_dir, page, "tts")
        summary["done"] += 1
    print(f"\n{summary}")
    return summary


def run(work_dir: Path, pages: set[int] | None = None) -> dict:
    with usage.recording(work_dir, "tts-gemini"):
        return _run(work_dir, pages)


if __name__ == "__main__":
    wanted = {int(a) for a in sys.argv[2:]} or None
    sys.exit(1 if (lambda s: s["failed"] or s["stopped_on_quota"])(run(Path(sys.argv[1]), wanted)) else 0)
