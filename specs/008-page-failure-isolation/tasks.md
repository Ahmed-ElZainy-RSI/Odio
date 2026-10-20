# Tasks: page failure isolation (CLI)

Tests first (`src/tests/test_page_failure_isolation.py`, 27 tests):
- [x] T001 [P] A fake failure on page 2 of 3: pages 1 and 3 are done, page 2 is recorded. Covered for Gemini OCR, LightOn OCR, Gemini TTS, Hume TTS and tashkel
- [x] T002 [P] A fake 429 on page 2 → run stops, nothing recorded, page untouched
- [x] T003 [P] A later success clears the recorded failure; status never changes on failure, so the stage retries the page
- [x] T004 [P] `TypeError` re-raises (CLI half of 007)

Implementation:
- [x] T005 `src/pipeline/failures.py`: `classify_failure`, `record`, `clear`, `handle`
- [x] T006 [P] `run_ocr_gemini.py` (rendering moved inside the try, so an unrenderable page is a page failure) and `run_ocr_lighton.py`
- [x] T007 [P] `run_speak_gemini.py`, `run_speak_hume.py`
- [x] T008 [P] `run_tashkel.py` (a local model crash on one page)
- [x] T009 Resume: no change needed. Status never advances on failure, so the existing resume checks retry the page
- [x] T010 `build_reader.py`: no change needed. It already lists pages without audio as skipped instead of crashing
- [x] T011 Schema: not needed. Failures live in `failures.json`, not in the page record (see spec § Design change)

Also changed: `test_ocr_resumability.py::test_a_genuinely_new_page_does_proceed_to_render`. It relied on a render error propagating. It now proves the same thing (the skip branch was not taken) through the recorded failure.

Every driver's `run()` now returns `{"done", "failed", "stopped_on_quota"}`. No existing caller used the old `None` return.

Verification (2026-10-20): `src/tests` 102 passed in `vx2lm` (was 75); 85 passed + 9 skipped in system Python; `ruff` clean on new files.
