# Implementation Plan: Book Pipeline

**Feature:** `001-book-pipeline` · **Date:** 2026-10-20 · **Spec:** [spec.md](spec.md)

## Summary

A linear, resumable, file-based pipeline: extract (text layer or OCR) → tokenise with stable
ids → route tashkeel → diacritise → TTS → bind timings → reader. Each stage is an importable
`run(...)` function plus an env-driven driver; `main.py` calls them in-process.

## Technical context

| | |
|---|---|
| Language | Python 3.10+ (`vx2lm` conda env for Gemini/whisper stages) |
| Dependencies | PyMuPDF (`fitz`), google-genai, requests, python-dotenv, arabic-diacritizer (torch, CPU), faster-whisper (`small`, int8, CPU), hume SDK |
| Storage | `work/<book>/pNNN.json`, `pages/pNNN.jpg`, audio files; no DB |
| Testing | pytest, `src/tests/`: 62 passed in the `vx2lm` env (2026-10-20, after 006). System Python skips the tests that need google-genai / fitz |
| Constraints | Free-tier quotas: Gemini TTS 10 req/day, Gemini 3.6 flash 20 req/day, Hume/SILMA credits exhausted |
| Scale | 36 books today; design point 1,000 (31k pages, 209 h audio, 12 GB MP3) |

## Constitution check

| Principle | Status | Note |
|---|---|---|
| I Correct before diacritize | ⚠️ | Base pipeline has rule cleaning only; real correction is `002`, run as a separate chain |
| II Stable token anchoring | ✅ | `p007w042` ids; tashkel abandons a page on count mismatch |
| III Uncertain ⇒ keep | ✅ | Untimed tokens stay unhighlighted; no guessed boxes on LightOn |
| IV Stage proves its lane | ✅ | Bind report, plus the letters-unchanged guard after diacritization (006, 2026-10-20) |
| V Per-page isolation | ❌ | Drivers `break`/`return` on first exception (`run_ocr_gemini.py`, `run_speak_*.py`). Resume is good; isolation is not |
| VI Free exact path | ✅ | 25 of 36 books need no OCR |
| VII Cost | ✅ | 1–2 paid calls/page |
| VIII Test without paying | ✅ | |
| IX Layering | n/a | Scripts; layering enforced in `003` |
| X Honest reporting | ✅ | README open items |

## Project structure

```
src/pipeline/
  main.py              entry point, text-layer detection, stage orchestration
  extract.py           text-layer words + boxes, clean_word rules
  ocr_gemini.py        Gemini vision, response_schema, line boxes → subdivided word boxes
  ocr_lighton.py       LightOn parse-v3, markdown → tokens, no boxes
  pages.py             page selector parsing / rendering helpers
  tashkel.py           arabic-diacritizer wrapper, positional re-bind
  tts_gemini.py        Gemini TTS → 24 kHz WAV
  tts_hume.py          Hume TTS → MP3 + native word timestamps
  align_whisper.py     faster-whisper transcription of our own audio
  speak.py             bind(): timings → token ids (anchor + interpolate)
  run_*.py             resumable drivers (BOOK=, PAGES=, VOICE= env)
  build_reader.py      reader.html generator
src/tests/             hermetic pytest suite
work/<book>/           outputs (gitignored audio)
```

## Phases

- Phase 0 research: done — `demo/ocr_benchmark.html`, `demo/OCR pricing dashboard.html`, tashkeel library note.
- Phase 1 design: [data-model.md](data-model.md), [contracts/page-record.schema.json](contracts/page-record.schema.json), [quickstart.md](quickstart.md).
- Phase 2: [tasks.md](tasks.md) (done items + remaining).
