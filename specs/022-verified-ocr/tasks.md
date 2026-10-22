# Tasks: verified OCR

Rewritten natively, not ported: nothing imports or wraps the walidapro prototype (it was
removed in the consolidation; its last state is the local tag `walidapro-final`). Only its
rules and thresholds were kept.

Tests first (providers faked):
- [x] T001 [P] `decide()` table tests: `tests/unit/test_verify_rules.py`
- [x] T002 [P] Blind-read request contains the crop only, never the OCR text or the candidate: `test_the_blind_reader_never_sees_the_ocr_text_and_fails_unclear`, `test_the_blind_reader_gets_only_the_crop`
- [x] T003 [P] UNCLEAR gates (confidence 0.72, single Arabic word, 0.12 gap, 0.82 support): `test_anything_short_of_clear_is_zero_evidence`
- [x] T004 [P] Token resolution: unique, repeated word pinned by its sentence, ambiguous → no verdict, never inside a longer token. (Box-overlap tie-break dropped: token boxes are line-subdivided approximations, too coarse to break ties)
- [x] T005 [P] Reviewer JSON repair; a failed review raises (not 0 suspects); quota is not retried
- [x] T006 [P] LightOn early stop only counts agreeing readings, never the candidate (`enough_votes`)

Implementation:
- [x] T010 `service/ai_engine/domain/verify_rules.py`: normalisation, the blind-read gates, variant consensus, `decide()`, suspect filtering, token resolution (pure, stdlib only)
- [x] T011 `adapters/ai/verifier/providers.py`: `GeminiReviewer` (suspects, context, validation)
- [x] T012 `adapters/ai/verifier/crops.py`: padded crop from the token box + 5 variants. Locator is injectable; the default uses the OCR token box (a PaddleOCR locator is a follow-up if line-subdivided boxes prove too coarse)
- [x] T013 `LightOnCropReader`, `GeminiBlindReader` (one attempt, failure = UNCLEAR)
- [x] T014 token resolution in the domain (T010)
- [x] T015 `adapters/ai/verifier/visual.py` `VisualVerifier`: assembles the stages, writes evidence (018) per verdict, reports `calls_made`, records usage (025), caps suspects per page
- [x] T016 Settings (`VERIFIER=visual|passthrough`, `VERIFIER_GEMINI_MODEL`, thresholds, `VERIFIER_MAX_SUSPECTS`) + `worker.py` wiring; missing keys fail the worker at startup; production config check counts the verifier as paid
- [ ] T017 Dockerfile `worker-verified` target (Pillow is already in the `ai` extra; needs a Docker daemon to verify)
- [ ] T018 e2e on a recorded page (needs real provider responses recorded once, with keys)
- [ ] T019 Persist verdicts per page (moved from 020 FR-005). Today `page.verified` is logged and evidence is on each verdict
- [x] T020 Provider cache (021) for the verifier's calls: `GeminiJson.ask` and `LightOnCropReader.read` key on provider, model, operation and digests of every input; only parsed replies are stored; a hit is not a paid call and is recorded as `cached`. Shares `PROVIDER_CACHE_DIR` with OCR/TTS

Verification (2026-10-22, vx2lm): service 847 passed including slow (was 785); `ruff` clean.
Branch: `feature/consolidate-walidapro`.
T020 (2026-10-22): service 855 passed including slow. Branch `feature/022-verifier-cache`.
