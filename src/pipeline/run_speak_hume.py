"""Layers 05-06 for one book: synthesise each page, bind word timings to ids.

Resumable: a page whose audio already exists is skipped, so an interrupted run
(rate limit, credit ceiling) costs at most one page on retry.
"""
import asyncio, base64, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from hume import AsyncHumeClient
from hume.tts import PostedUtterance, PostedUtteranceVoiceWithName
from pipeline.speak import bind

BOOK = "النجوم"
VOICE = "Layla, Arabic Philosopher"
WORK = Path(f"work/{BOOK}")
AUDIO = Path(f"work/{BOOK}/audio")

async def do_page(client, path):
    rec = json.loads(path.read_text(encoding="utf-8"))
    mp3 = AUDIO / f"p{rec['page']:03d}.mp3"

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
    r = await client.tts.synthesize_json(
        utterances=[PostedUtterance(text=text,
            voice=PostedUtteranceVoiceWithName(name=VOICE, provider="HUME_AI"))],
        version="2", include_timestamp_types=["word"])
    g = r.generations[0]
    words = [{"text": t.text, "b": t.time.begin, "e": t.time.end}
             for s in [x for grp in (g.snippets or []) for x in grp]
             for t in (s.timestamps or [])]

    tokens, report = bind(rec["tokens"], words, int(g.duration * 1000))
    AUDIO.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(base64.b64decode(g.audio))

    rec.update({"tokens": tokens, "tts": "hume", "voice": VOICE,
                "hume_words": words,   # persisted so re-binding needs no API call
                "align": "native-word-timestamps",
                "duration_ms": int(g.duration * 1000),
                "audio": f"audio/p{rec['page']:03d}.mp3",
                "bind_report": report, "status": "spoken"})
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  p{rec['page']:03d}  {g.duration:>6.2f}s  "
          f"hume {report['hume_timestamps']:>3} ts -> {report['anchored']:>3} anchored + "
          f"{report['interpolated']:>3} interp = {report['tokens']:>3}  "
          f"{'mono' if report['monotonic'] else 'NON-MONOTONIC'}")

async def main():
    client = AsyncHumeClient(api_key=os.environ["HUME_API_KEY"])
    pages = sorted(WORK.glob("p*.json"))
    print(f"{BOOK} — {len(pages)} pages, voice: {VOICE}\n")
    for p in pages:
        try:
            await do_page(client, p)
        except Exception as e:
            print(f"  {p.name}  FAILED: {type(e).__name__}: {str(e)[:150]}")
            print("  stopping — rerun to resume from here")
            return
    print("\ndone")

if __name__ == "__main__":
    asyncio.run(main())
