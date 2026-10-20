# Tasks: OCR Correction

## Done

- [x] T001 [US1] Engine diff with diacritic folding — `tools/ensemble_review.py`
- [x] T002 [US1] Page-level VLM arbitration — `tools/vlm_arbiter.py`
- [x] T003 [US1] Reversible word-level apply, letters-only idempotency — `tools/apply_corrections.py`
- [x] T004 [US2] Page replacement ≥50% gemini-only — `tools/replace_page.py`
- [x] T005 [US3] Correlated-error detection + HTML review + human-gated apply — `context_detector.py`, `build_context_review.py`, `apply_context_suggestions.py`
- [x] T006 [US4] Orchestrator, idempotent — `tools/run_full_pipeline.py`

## Remaining. Each one is its own feature

- [ ] [017-vlm-third-answer-routing](../017-vlm-third-answer-routing/)
- [ ] [018-correction-evidence-record](../018-correction-evidence-record/)
- [ ] [019-correction-accuracy-harness](../019-correction-accuracy-harness/)
- [ ] Blind-crop verification of `differ` tokens comes with [022-verified-ocr](../022-verified-ocr/) (the verifier can be called from `tools/` once it exists)
