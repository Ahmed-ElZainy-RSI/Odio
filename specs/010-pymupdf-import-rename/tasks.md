# Tasks: pymupdf import rename

- [x] T001 Call sites listed: 12 files across `src/pipeline`, `src/tests`, `tools`, `service/ai_engine/adapters/ai`, `service/tests`
- [x] T002 [P] Renamed in `service/` adapters (`renderer_pymupdf.py`, `ocr_textlayer.py`) and service tests
- [x] T003 [P] Renamed in `src/pipeline/` (`main.py`, `ocr_gemini.py`, `run_extract.py`, `run_ocr_gemini.py`), `src/tests/` and the `tools/run_full_pipeline.py` docstring
- [x] T004 Pinned `pymupdf>=1.24.3` in the service `ai` extra. The root `requirements.txt` doesn't list PyMuPDF at all; that belongs to 011 (lock files)
- [x] T005 Suites: `src/tests` 137 passed (vx2lm), 120 + 9 skipped (system Python); service 645 passed including slow; `ruff` clean. `grep -rn '\bfitz\b'` → 0 hits

Branch: `feature/010-pymupdf-import-rename`, merged into `dev`.
