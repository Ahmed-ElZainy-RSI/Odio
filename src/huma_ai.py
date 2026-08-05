import asyncio
import base64
import os
from pathlib import Path

from dotenv import load_dotenv
from hume import AsyncHumeClient
from hume.tts import PostedUtterance, PostedUtteranceVoiceWithName

load_dotenv()

api_key = os.getenv("HUME_API_KEY")
if not api_key:
    raise EnvironmentError("HUME_API_KEY not found in environment variables.")

OUTPUT_DIR = Path("assets/samples_tashkel/hume")
VOICE = PostedUtteranceVoiceWithName(name="Ava Song", provider="HUME_AI")

# Same 449-character passage used for every other engine in
# assets/samples_tashkel/sam_1/, read from disk rather than retyped, so this
# Hume run is directly comparable to those and never drifts from the source.
ARABIC_TEXT = Path("assets/samples_tashkel/sam_1/sam_1.md").read_text(encoding="utf-8").strip()

UTTERANCES = {
    "english": "Dogs became domesticated between 23,000 and 30,000 years ago.",
    "arabic": ARABIC_TEXT,
}


async def synthesize(hume: AsyncHumeClient, label: str, text: str) -> None:
    utterance = PostedUtterance(text=text, voice=VOICE)
    result = await hume.tts.synthesize_json(utterances=[utterance], version="2")
    generation = result.generations[0]

    out_path = OUTPUT_DIR / f"{label}.mp3"
    out_path.write_bytes(base64.b64decode(generation.audio))
    print(f"  {label}: {generation.duration:.1f}s -> {out_path}")


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    hume = AsyncHumeClient(api_key=api_key)
    for label, text in UTTERANCES.items():
        await synthesize(hume, label, text)


if __name__ == "__main__":
    asyncio.run(main())
