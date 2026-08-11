"""Layers 05-06 for one book via Hume TTS (native word timestamps).

Resumable: a page whose audio already exists is skipped, so an interrupted run
(rate limit, credit ceiling) costs at most one page on retry.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from hume import AsyncHumeClient

from pipeline.speak import bind
from pipeline.tts_hume import synthesize, VOICE
from pipeline.pages import parse_pages, in_range


async def do_page(client, path, audio_dir, voice):
    rec = json.loads(path.read_text(encoding="utf-8"))
    mp3 = audio_dir / f"p{rec['page']:03d}.mp3"

    if rec["page"] == 1:
        # front matter: title, deposit number, ISBN. Not narration.
        rec["status"] = "skipped-front-matter"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  p{rec['page']:03d}  front matter, skipped")
        return
    if mp3.exists() and rec.get("status") == "spoken":
        print(f"  p{rec['page']:03d}  already done, skipping")
        return

    text = " ".join(t["txt"] for t in rec["tokens"])
    audio, words, duration_ms = await synthesize(client, text, voice)
    tokens, report = bind(rec["tokens"], words, duration_ms)
    audio_dir.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(audio)

    rec.update({"tokens": tokens, "tts": "hume", "voice": voice,
                "hume_words": words,   # persisted so re-binding needs no API call
                "align": "native-word-timestamps",
                "duration_ms": duration_ms,
                "audio": f"audio/p{rec['page']:03d}.mp3",
                "bind_report": report, "status": "spoken"})
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  p{rec['page']:03d}  {duration_ms/1000:>6.2f}s  "
          f"hume {report['hume_timestamps']:>3} ts -> {report['anchored']:>3} anchored + "
          f"{report['interpolated']:>3} interp = {report['tokens']:>3}  "
          f"{'mono' if report['monotonic'] else 'NON-MONOTONIC'}")


async def _run_async(work_dir: Path, voice: str, pages):
    client = AsyncHumeClient(api_key=os.environ["HUME_API_KEY"])
    audio_dir = work_dir / "audio"
    start, end = parse_pages(pages)
    all_pages = sorted(work_dir.glob("p*.json"))
    page_files = [f for f in all_pages if in_range(int(f.stem[1:]), start, end)]
    print(f"{work_dir.name} — {len(page_files)} of {len(all_pages)} pages, voice: {voice}\n")
    for p in page_files:
        try:
            await do_page(client, p, audio_dir, voice)
        except Exception as e:
            print(f"  {p.name}  FAILED: {type(e).__name__}: {str(e)[:150]}")
            print("  stopping — rerun to resume from here")
            return
    print("\ndone")


def run(work_dir: Path, voice: str = VOICE, pages="0"):
    """Synthesise pages in work_dir. pages: "0" = all, "5" = pages 1-5, "13:20" = pages 13-20."""
    asyncio.run(_run_async(work_dir, voice, pages))


def main():
    book = os.environ.get("BOOK", "النجوم")
    voice = os.environ.get("VOICE", VOICE)
    run(Path(f"work/{book}"), voice, pages=os.environ.get("PAGES", "0"))


if __name__ == "__main__":
    main()
