# Quickstart: Book Pipeline

Run from the repo root. Needs `.env` with `GEMINI_API_KEY` (and `HUME_API_KEY` /
`LIGHTON_API_KEY` for those engines).

```bash
# whole pipeline, defaults: Gemini OCR (if scanned), Gemini TTS voice Kore
python3 src/pipeline/main.py "assets/PDF Samples/النجوم.pdf"

# choose engines, cap extraction to pages 13–20
python3 src/pipeline/main.py "assets/PDF Samples/book.pdf" --ocr lighton --tts hume --pages 13:20

# text layer exists but is wrong
python3 src/pipeline/main.py "assets/PDF Samples/شكرا يامى.pdf" --force-ocr
```

Stage by stage (each resumable):

```bash
BOOK="النجوم" python3 src/pipeline/run_extract.py
BOOK="<book>" PAGES=5 python3 src/pipeline/run_ocr_gemini.py
BOOK="<book>" python3 src/pipeline/run_tashkel.py
BOOK="<book>" python3 src/pipeline/run_speak_gemini.py
BOOK="<book>" python3 src/pipeline/build_reader.py
```

Verify: open `work/<book>/reader.html`; click a word → audio seeks; play → highlight follows.

Tests: `python3 -m pytest -q` (root `pytest.ini` → `src/tests`).
