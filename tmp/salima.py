import os
import re
import time
import wave
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SALIMA_API_KEY")
if not api_key:
    raise EnvironmentError("SALIMA_API_KEY not found in environment variables.")

BOOK_PATH = Path("assets/samples_tashkel/sam_2/الشمس.txt")
OUTPUT_DIR = Path("assets/samples_tashkel/salima/الشمس")
PAGES_TO_READ = 4
CHAR_LIMIT = 250  # SILMA /tts/v2/stream's documented per-request cap
REQUEST_INTERVAL = 1.1  # observed rate limit on this endpoint: 1 request/second

MODEL_ID = "silma-tts-v2-msa"  # book is formal, fully-diacritized Arabic, not dialect
VOICE_ID = "salma"

sample_rate = 24000
persistent_session = requests.Session()

headers = {
    'Accept': "application/json",
    'Content-Type': "application/json",
    'apiKey': api_key,
}


class OutOfCredits(Exception):
    """Raised when SILMA rejects a request for lack of character credits —
    every later request will fail identically until the quota resets, so the
    whole run stops here instead of grinding through the rest of the book."""


def load_book_pages(path: Path, max_pages: int) -> list[str]:
    """Split the OCR'd book on its '--- Page N ---' markers and keep the
    first max_pages. parts[0] is whatever precedes 'Page 1' (empty here)."""
    raw = path.read_text(encoding="utf-8")
    parts = re.split(r"-{2,}\s*Page\s*\d+\s*-{2,}", raw)
    return parts[1:1 + max_pages]


def clean_page_text(raw: str) -> str:
    """Strip everything that isn't actual book prose: the OCR pipeline's own
    commentary about images, markdown image/div wrappers, heading markers,
    and bare page-number lines — none of that should be read aloud."""
    text = re.sub(r"(?m)^Note:.*$", "", raw)
    text = re.sub(r"<div[^>]*>.*?</div>", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*\d+\s*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def chunk_text(text: str, limit: int) -> list[str]:
    """Split into pieces <= limit chars, breaking on sentence boundaries
    where possible so no single request ever hits SILMA's 250-char cap.
    Falls back to word-packing for any single sentence longer than limit."""
    sentences = re.split(r"(?<=[.؟!])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > limit:
            for word in sentence.split(" "):
                candidate = f"{current} {word}".strip() if current else word
                if len(candidate) <= limit:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = word
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def stream_waveform(text: str):
    """POST one <=250-char chunk to SILMA and yield float32 waveform pieces
    as they stream back. Raises OutOfCredits if the account is out of quota;
    any other non-200 just yields nothing for this one chunk."""
    payload = {
        "model_id": MODEL_ID,
        "text": text,
        "creativity": 0.2,
        "speed": 0.2,
        "voice_id": VOICE_ID,
    }
    carry_over = b""
    with persistent_session.post(
        "https://api.silma.ai/tts/v2/stream", json=payload, stream=True, headers=headers
    ) as r:
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", "")
            except requests.exceptions.JSONDecodeError:
                detail = r.text
            print(f"    FAILED (HTTP {r.status_code}): {detail}")
            if "credit" in str(detail).lower():
                raise OutOfCredits(detail)
            return

        for chunk in r.iter_content(chunk_size=None):
            if not chunk:
                continue
            current_data = carry_over + chunk
            cut_off = (len(current_data) // 4) * 4
            valid_bytes, carry_over = current_data[:cut_off], current_data[cut_off:]
            if valid_bytes:
                yield np.frombuffer(valid_bytes, dtype=np.float32)


def synthesize_page(page_num: int, text_chunks: list[str]) -> np.ndarray:
    """Returns the full page waveform, or raises OutOfCredits partway through
    — callers must not save a partial result as if it were the whole page."""
    page_audio = []
    for i, chunk in enumerate(text_chunks, 1):
        print(f"  page {page_num}, request {i}/{len(text_chunks)} ({len(chunk)} chars)")
        pieces = list(stream_waveform(chunk))
        if pieces:
            page_audio.append(np.concatenate(pieces))
        time.sleep(REQUEST_INTERVAL)  # stay under the endpoint's ~1 req/s limit
    return np.concatenate(page_audio) if page_audio else np.array([], dtype=np.float32)


def save_wav(path: Path, waveform: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(waveform, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def main():
    raw_pages = load_book_pages(BOOK_PATH, PAGES_TO_READ)
    print(f"Read {len(raw_pages)} page(s) from {BOOK_PATH.name}\n")

    for page_num, raw in enumerate(raw_pages, 1):
        out_path = OUTPUT_DIR / f"page_{page_num:02d}.wav"
        if out_path.exists():
            print(f"page {page_num}: {out_path.name} already exists, skipping "
                  f"(delete it to force a re-run)\n")
            continue

        text = clean_page_text(raw)
        if not text:
            print(f"page {page_num}: empty after cleanup, skipping\n")
            continue

        chunks = chunk_text(text, CHAR_LIMIT)
        print(f"page {page_num}: {len(text)} chars -> {len(chunks)} request(s)")

        try:
            waveform = synthesize_page(page_num, chunks)
        except OutOfCredits as e:
            print(f"\nOut of SILMA character credits partway through page {page_num}.")
            print("Stopping here rather than saving a page that's silently missing audio "
                  "and burning more requests that would fail the same way.")
            print(f"Detail: {e}")
            print(f"\nAlready-saved pages are untouched. Re-run this script once your "
                  f"credits reset — completed pages are skipped automatically.")
            return

        if waveform.size == 0:
            print(f"  no audio received, nothing saved\n")
            continue

        save_wav(out_path, waveform)
        print(f"  -> {out_path} ({waveform.size / sample_rate:.1f}s)\n")

    print("Done.")


if __name__ == "__main__":
    main()
