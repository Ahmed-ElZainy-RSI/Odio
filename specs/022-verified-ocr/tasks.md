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
- [x] T017 No separate image target needed: the one image already carries everything the verifier uses (Pillow 12.1.1 via the runtime lock; no PaddleOCR). Verified 2026-10-24 with Colima: crops run in the image; `ai-engine-worker` with `ENABLE_VERIFIED_TIER=true` refuses to start without both keys and starts with them (also with `FINAL_REVIEW=suggest`). The build itself had been broken since 025 (`.dockerignore` excluded `config/`), fixed alongside
- [ ] T018 e2e on a recorded page (needs real provider responses recorded once, with keys)
- [ ] T019 Persist verdicts per page (moved from 020 FR-005). Today `page.verified` is logged and evidence is on each verdict
- [x] T020 Provider cache (021) for the verifier's calls: `GeminiJson.ask` and `LightOnCropReader.read` key on provider, model, operation and digests of every input; only parsed replies are stored; a hit is not a paid call and is recorded as `cached`. Shares `PROVIDER_CACHE_DIR` with OCR/TTS

Verification (2026-10-22, vx2lm): service 847 passed including slow (was 785); `ruff` clean.
Branch: `feature/consolidate-walidapro`.
T020 (2026-10-22): service 855 passed including slow. Branch `feature/022-verifier-cache`.

Arabic LLM handling brought to the prototype's level (2026-10-22, branch `feature/022-arabic-llm-handling`):
- [x] T021 Prompts carried over word for word (`adapters/ai/verifier/prompts.py`; a check against tag `walidapro-final` confirms all 8 are identical). The reviewer asks for `{"total_words", "items"}`, with page number and threshold in the prompt
- [x] T022 The context judge receives the LightOn visual reading; the validator receives the sentence before **and after** the replacement (`verify_rules.replace_in_sentence`, Arabic-word boundaries; a word absent or repeated in its sentence → REVIEW `ambiguous-in-sentence`)
- [x] T023 Strict reading of the model's output: `unclear` missing → **True**, `"false"`/`"no"` → False (`to_bool`); a non-numeric or out-of-range score/confidence is clamped, 0.0 when unreadable (`to_score`)
- [x] T024 Output-token ceilings per call: review 6000, scores 1000, blind read 120
- [x] T025 Retries only for temporary failures (network, 408, 5xx, empty/unparseable reply), at most 3, backoff `min(1.5·2^(n-1) + jitter, 5 s)`; a permanent 4xx is not retried; 429/503 stay `ModelUnavailableError` (the job's retry policy handles quota)
- [x] T026 One word's context/validation failure → that word REVIEW `context-validation-failed`, the page continues (quota still fails the page for a later retry)
- [x] T027 Parity details: `__` stripped from LightOn text; crop upscale capped at ×4

Deliberately stricter than the prototype: a review reply without an `items` list is a failed review (the prototype read it as zero suspects, contrary to its own rule).
Watch on the first real run: `maxOutputTokens=120` on the blind read assumes the model does not spend that budget on thinking; if blind reads come back empty, raise it or disable thinking for that call.

Verification: service 883 passed including slow; `ruff` clean.

