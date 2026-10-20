# Tasks: VLM third-answer routing

- [x] T001 Decide the noise-only rule: not needed, because `normalize_for_compare` already folds punctuation and keeps digits (spec acceptance 3)
- [x] T002 [P] Tests: matches applied; third answer, ISBN re-guess, dropped digit and multi-word routed; reviewer decisions survive reruns; decisions file merged, not replaced; no queue file when nothing routed — `src/tests/test_apply_corrections.py` (+10)
- [x] T003 `classify_verdict()` in `tools/apply_corrections.py` (done at apply time instead of in `vlm_arbiter.py`, so existing `ensemble_review.json` files work without re-arbitrating, at zero API cost)
- [x] T004 `apply_corrections.py` routing → `vlm_review.json` + `context_decisions.json` seeded `"reject"`; summary prints counts by agreement and the routed total
- [x] T005 Review HTML: VLM entries merged, tagged, counted — `tools/build_context_review.py` (+1 test); applied on accept with `vlm-reviewed` — `tools/apply_context_suggestions.py` (+2 tests)
- [ ] T006 Re-run on the pilot book and record counts here. Needs `work/<book>-lighton/ensemble_review.json` back on disk (`work/` and `output_work/` are not present right now)
- [x] T007 [P] FR-005: verdicts stripped of surrounding whitespace before use (test with the real `'يَصْرُخُ؟! '` case)
- [ ] T008 Repair `فلفل المغرور-lighton` `p010w014` (strip the trailing space) once `work/` is back on disk. Re-running `apply_corrections.py` will not fix it, because the letters already match and it's a no-op

Verification (2026-10-20): `src/tests` 75 passed in `vx2lm` (was 62); 66 passed + 1 skipped in system Python. Ruff shows 5 findings in these files, all pre-existing (I001 import order in two test files, RUF100 unused `noqa` in three tools), and none were introduced.
