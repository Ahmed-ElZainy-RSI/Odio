# Tasks: page failure isolation (CLI)

Tests first:
- [ ] T001 [P] Fake OCR raising on page 2/4 → 1, 3, 4 done, 2 failed
- [ ] T002 [P] Fake 429 on page 2 → stop, page 2 not marked failed
- [ ] T003 [P] Re-run retries `failed` pages
- [ ] T004 [P] `TypeError` re-raises (with 007)

Implementation:
- [ ] T005 `src/pipeline/failures.py`
- [ ] T006 [P] `run_ocr_gemini.py`, `run_ocr_lighton.py`
- [ ] T007 [P] `run_speak_gemini.py`, `run_speak_hume.py`
- [ ] T008 [P] `run_tashkel.py`
- [ ] T009 Resume checks treat `failed` as not done
- [ ] T010 `build_reader.py` placeholder for failed pages
- [ ] T011 Add `failed` + `error` to the page-record schema (with 009)
