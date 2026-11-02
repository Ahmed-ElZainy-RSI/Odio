# Tasks: review in context

- [x] T001 [P] Tests first: `src/tests/test_review_context.py`: phrase boundaries and span, Gemini-only rows, backfill keeps verdicts, page shows phrase and controls, server round trip and refusals, undo / replace / keep of an automatic fix, stale skip
- [x] T002 `ensemble_review.phrase_around` + `context` on non-KEEP rows; backfill on already-compared pages (FR-001, FR-002)
- [x] T003 `context_detector.py`: `context` on new entries; backfill existing entries without an API call (FR-001, FR-002)
- [x] T004 `build_context_review.py`: phrase, readings, three controls, automatic-fix group, decisions embedded as data; Save (server) or Download (file) (FR-004, FR-005)
- [x] T005 `build_context_review.py --serve`: localhost server, validated atomic `POST` (FR-005, SC-004)
- [x] T006 `apply_context_suggestions.py`: decisions on automatic fixes (FR-003, FR-006)
- [x] T007 Docs: README correction-chain section, `docs/full-pipeline-guide.html` review step, `specs/README.md` row

Verification (2026-11-02, vx2lm): `src/tests` 233 passed (23 new in `test_review_context.py`).
On the real book `داخلي ما الكَهْرَبَاءُ؟` (pages 12–16) the re-runs gave 26 image fixes and 31 LLM
suggestions their phrases with no API call; the page lists 57 entries; `--serve` served the page and
its image and answered `api/decisions`.
Branch: `feature/034-review-in-context`.
