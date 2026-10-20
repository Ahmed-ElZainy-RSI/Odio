# Tasks: Book Pipeline

**Input:** `specs/001-book-pipeline/`

## Done (retro-spec evidence)

- [x] T001 [US1] Text-layer extraction with word boxes + rule cleaning — `src/pipeline/extract.py`, `run_extract.py`
- [x] T002 [US1] Gemini OCR with forced schema, line boxes — `ocr_gemini.py`
- [x] T003 [US1] LightOn OCR, text only, HTML/LaTeX stripping — `ocr_lighton.py`
- [x] T004 [US1] Tashkeel router on `diac_ratio` 0.15 — `run_extract.py`, `run_ocr_*.py`
- [x] T005 [US1] Local diacritizer with positional re-bind — `tashkel.py`
- [x] T006 [US1] Gemini TTS + faster-whisper alignment — `tts_gemini.py`, `align_whisper.py`
- [x] T007 [US1] Hume TTS native timestamps — `tts_hume.py`
- [x] T008 [US1] `speak.bind` anchor + interpolate — `speak.py`
- [x] T009 [US1] Reader with image/text views, box-free fallback — `build_reader.py`
- [x] T010 [US2] Resume checks recognise all later states — `run_tashkel.py`, `run_ocr_*.py`
- [x] T011 [US3] `--force-ocr` — `main.py`

## Remaining. Each one is its own feature

- [ ] [008-page-failure-isolation](../008-page-failure-isolation/)
- [ ] [006-letter-preservation-guard](../006-letter-preservation-guard/)
- [ ] [009-page-record-schema-validation](../009-page-record-schema-validation/)
- [ ] [012-text-layer-trust-check](../012-text-layer-trust-check/)
- [ ] [013-content-based-book-id](../013-content-based-book-id/)
- [ ] [010-pymupdf-import-rename](../010-pymupdf-import-rename/)
- [ ] [014-front-matter-handling](../014-front-matter-handling/)
- [ ] [015-parallel-book-runner](../015-parallel-book-runner/)
- [ ] [011-dependency-lockfile](../011-dependency-lockfile/)
- [ ] T026 README test count: 56 was right under `vx2lm` (system Python skips the tests whose packages are missing). The count is now 62 after 006. Update `README.md` and `TESTING.md`, and say which env to run in
