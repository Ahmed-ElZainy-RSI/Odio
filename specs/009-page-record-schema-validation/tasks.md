# Tasks: page record schema validation

- [x] T001 Schema rewritten to match reality (`src/pipeline/page-record.schema.json`, copied to the 001 contract). The first version followed the README sketch and was wrong on `t_src` (`interpolated`, not `interp`), untimed tokens (no `t` key, not `null`), `box` (`[x, y, w, h]` percentages), `fixes` (per-token strings, not record-level objects), `box_level` (absent on the text layer) and id width. Added `skipped-empty`, `skipped-front-matter`, `diac_error`; no `failed` status (008 uses `failures.json`)
- [x] T002 [P] Tests — `src/tests/test_records.py` (35): valid real shapes; 11 invalid cases including the US-spelling status typo and the real `'يَصْرُخُ؟! '` defect; fallback validator; strict/warn/off; byte-identical output format; atomic write; audit; single-writer grep test; schema copies in sync
- [x] T003 `src/pipeline/records.py`: `validate`, `read_record`, `write_record` (atomic), `audit` + CLI. Uses `jsonschema` when installed, otherwise a fallback check driven by the same schema file
- [x] T004 All 9 writers in `src/pipeline/` go through `write_record` (`run_extract`, `run_ocr_gemini`, `run_ocr_lighton`, `run_tashkel` ×2, `run_speak_gemini` ×2, `run_speak_hume` ×2)
- [x] T005 All 3 writers in `tools/` go through it (`apply_corrections`, `apply_context_suggestions`, `replace_page`)
- [x] T006 Grep test: no direct record writes outside `records.py`
- [x] T007 Audit of the real corpus: the 92 page records recovered from git history (6 books) → **0 invalid**, with both `jsonschema` and the fallback. `work/` itself isn't on disk now; run `python3 src/pipeline/records.py work/` once it is

Also: `src/tests/conftest.py` runs every test in strict mode, and `write_page()` now writes schema-valid records (`source`, `diac_ratio`, `path` defaults). [001 data-model.md](../001-book-pipeline/data-model.md) was corrected to match.

Verification (2026-10-20): `src/tests` 137 passed in `vx2lm` (was 102); 120 passed + 9 skipped in system Python; service unchanged.
