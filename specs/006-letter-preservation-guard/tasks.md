# Tasks: letter preservation guard

Tests first:
- [x] T001 [P] Property tests (hypothesis): marks-only edits pass; letter/punct edits fail — `service/tests/unit/test_preservation.py` (13 tests)
- [x] T002 [P] Fake-model test: tashkel output with a swapped letter → page untouched, `diac_error` set — `src/tests/test_tashkel_preservation.py` (5 tests)

Implementation:
- [x] T003 Resolve FR-002: hamza/madda count as letters, NFC first (recorded in spec.md)
- [x] T004 `service/ai_engine/domain/preservation.py` (ported from walidapro `rebuild_text.py`, stdlib only; passes `test_domain_purity.py`)
- [x] T005 Copy to `src/pipeline/preservation.py` + sync test `src/tests/test_preservation_copy_in_sync.py`
- [x] T006 Called from `tashkel.diacritize_tokens`; report carries `preserved` and `first_diff` (with token id)
- [x] T007 `run_tashkel.py` writes `diac_error` on rejection, clears it on success, counts rejected pages; `run()` now returns 4 values (no caller used the return)
- [x] T008 Measured on 95 real pages with the real model: 0 letter changes by the model, 1 rejection caused by an upstream whitespace defect → [measurement.md](measurement.md)

Remaining (owned by other features):
- [ ] Service diacritizer adapter calls the guard → [016](../016-service-diacritization/)
- [ ] Visual diacritics call the guard → [023](../023-visual-diacritics/)
- [ ] Add `diac_error` to the page-record schema → [009](../009-page-record-schema-validation/)

Verification (2026-10-20, `vx2lm` env): `src/tests` 62 passed (was 56); `service` 620 passed, 9 slow deselected (was 607); `ruff check` + `ruff format --check` clean.
