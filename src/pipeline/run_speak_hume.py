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

from pipeline import failures, usage
from pipeline.records import write_record
from pipeline.speak import bind
from pipeline.tts_hume import synthesize, VOICE


async def do_page(client, path, audio_dir, voice):
    rec = json.loads(path.read_text(encoding="utf-8"))
    mp3 = audio_dir / f"p{rec['page']:03d}.mp3"

    if rec["page"] == 1:
        # front matter: title, deposit number, ISBN. Not narration.
        rec["status"] = "skipped-front-matter"
        write_record(path, rec)
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
    write_record(path, rec)
    print(f"  p{rec['page']:03d}  {duration_ms/1000:>6.2f}s  "
          f"hume {report['hume_timestamps']:>3} ts -> {report['anchored']:>3} anchored + "
          f"{report['interpolated']:>3} interp = {report['tokens']:>3}  "
          f"{'mono' if report['monotonic'] else 'NON-MONOTONIC'}")


async def _run_async(work_dir: Path, voice: str):
    client = AsyncHumeClient(api_key=os.environ["HUME_API_KEY"])
    audio_dir = work_dir / "audio"
    all_pages = sorted(work_dir.glob("p*.json"))
    print(f"{work_dir.name} — {len(all_pages)} pages, voice: {voice}\n")
    summary = {"done": 0, "failed": 0, "stopped_on_quota": False}
    for p in all_pages:
        page = int(p.stem[1:])
        try:
            with usage.page(page):
                await do_page(client, p, audio_dir, voice)
        except Exception as e:
            # recorded and skipped; Hume's credit ceiling counts as quota and stops (spec 008)
            if failures.handle(work_dir, page, "tts", e, f"  {p.name}") == "quota":
                summary["stopped_on_quota"] = True
                break
            summary["failed"] += 1
            continue
        failures.clear(work_dir, page, "tts")
        summary["done"] += 1
    print(f"\n{summary['failed']} failed" if summary["failed"] else "\ndone")
    return summary


def _run(work_dir: Path, voice: str = VOICE):
    """Synthesise every page in work_dir that isn't already spoken.

    No page-range option here on purpose: --pages/PAGES only controls which
    pages get fetched during extraction/OCR. Once pages exist on disk, this
    processes everything not yet spoken, so it also catches up any earlier
    pages left unsynthesised instead of hiding that backlog behind a filter.
    """
    return asyncio.run(_run_async(work_dir, voice))


def run(work_dir: Path, voice: str = VOICE):
    """Synthesise every unspoken page, recording every paid call to usage.jsonl (spec 025)."""
    with usage.recording(work_dir, "tts-hume"):
        return _run(work_dir, voice)


def main():
    book = os.environ.get("BOOK", "النجوم")
    voice = os.environ.get("VOICE", VOICE)
    run(Path(f"work/{book}"), voice)


if __name__ == "__main__":
    main()
