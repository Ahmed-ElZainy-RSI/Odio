# Our pipeline vs `walidapro/` — difference report

> **Update 2026-10-22:** the merge described in §9 is done for the parts worth keeping, and
> `walidapro/` is removed. See [§10](#10-where-each-part-went-2026-10-22).

**Date:** 2026-10-20 · **Scope:** `src/pipeline/` + `tools/` + `service/` (ours) vs `walidapro/` (Waleed's drop-in)
**Builds on:** `docs/backend-v1/01-current-architecture.md` §2 and ADR-009, which predate
walidapro's API layer (commit `85ca665`). This report updates them.
**Action plan:** `specs/004-verified-tier-walidapro/`.

---

## 1. Bottom line

1. **The two do different jobs well, and neither covers the other.** Ours is cheap, has the
   free text-layer path, stable token ids and **word timings**, so it is the only one that can
   produce the read-along reader and the Job B `JSON` timing artifact. Walidapro is a much more
   careful **OCR verifier and diacritizer**. It never changes a letter without blind visual
   evidence, and every word decision is auditable. It also costs ~40–200 paid calls per page
   against our 1–2, and it has no word timings.
2. **There are now two implementations of the same Backend contract.** `walidapro/api/` and
   `service/ai_engine/` both implement `POST /api/jobs` with Keycloak and the 3-step upload.
   Ours is complete and tested. Walidapro's is missing its worker module (`processing.py` is
   0 bytes), so every accepted job would fail. Keep one: **`service/`**.
3. **Walidapro does not run as committed.** 6 non-marker `.py` files are 0 bytes. Because of
   them, the API does not start, `run_full_pipeline.py` fails at import, TTS is dead, and pytest
   cannot collect. Only `run.py` (OCR verification) works.
4. **What to do:** keep our pipeline as the default (fast tier). Port walidapro's
   **correctness engine** (`ocr_verifier/` stages 2–4) behind the `PageVerifier` port as the
   opt-in **verified tier**. Drop walidapro's API layer, CLI orchestrator and TTS in favour of
   ours. See §9.

---

## 2. What each one is

| | **Ours** | **walidapro** |
|---|---|---|
| Author / origin | maintained core, evolved in-repo | single drop-in commit `eb1e047` "add walid code" (2026-08-24), API + tests added in `85ca665` |
| Code size | `src/pipeline` ≈ 1.7k LOC · `tools/` 1.75k · `service/` 8.7k prod + 8.5k tests | `ocr_verifier/` 12.5k · `api/` 1.3k · `run*.py` 3.0k · tests 0.55k (17.3k total) |
| Entry points | `src/pipeline/main.py`, `tools/run_full_pipeline.py`, `ai-engine-api` / `-worker` / `-reconciler` | `run.py` (OCR only), `run_full_pipeline.py` (2,911-line orchestrator), `run_api.py` |
| Imports between them | `tools/` → `pipeline`; `service` adapters → `pipeline` | none in either direction (closed system) |
| Tests | `src/tests`: **47 passed, 1 skipped** (run 2026-10-20) · `service/tests`: 616 per status doc (needs its venv) | 4 test files; **collection fails** (`conftest.py` empty, `requests_mock` not installed) |
| Packaging / deploy | `service/pyproject.toml`, Dockerfile, compose, CI | `requirements.txt` with no pins; PaddlePaddle installed by hand |

---

## 3. Stage-by-stage

| Stage | Ours | walidapro | Who's ahead |
|---|---|---|---|
| **Text-layer fast path** | Yes. PyMuPDF words + per-word boxes, 0 paid calls, used by 25/36 books. `--force-ocr` for lying layers | `pdf_type_detector.py` classifies pages (`TEXT_LAYER`/`OCR_REQUIRED`/`MIXED`), but **`run_full_pipeline.py` never calls it**. Every page is rasterized and OCR'd | **Ours** |
| **Render** | PyMuPDF | pypdfium2 @ 220 DPI, detached images | tie |
| **OCR** | Gemini `gemini-3.6-flash` with response schema (line boxes, subdivided) or LightOn parse-v3 (no boxes) | LightOn parse-v3 only, with 429/5xx backoff and a fingerprinted cache (PDF SHA-256, DPI, model, max_side) | ours = choice + boxes; W = better caching |
| **Error detection** | Offline. Two-engine diff (`ensemble_review`) + text-only correlated detector (`context_detector`) | In-process. LLM `ContextReviewer` flags suspicious words per page, with JSON repair and no silent "0 suspects" | different signals. W catches single-engine errors; ours catches cross-engine disagreement |
| **Localization** | Gemini line boxes (subdivided by letter count) | PaddleOCR PP-OCRv5, used **for boxes only** and never as text | **W** (true word boxes on scanned pages) |
| **Verification** | `vlm_arbiter`: one vision call per flagged **page**, and the model sees the page | Per word: 5 crop variants, adaptive LightOn re-reads (2–5, stopping without seeing the candidate), then a **blind** Gemini crop read where `UNCLEAR` counts as zero evidence | **W** (stronger; our VLM is not blind) |
| **Decision** | apply if VLM verified; human gate for the context layer | `decision.decide()` weighted fusion with hard gates (e.g. visual ≥ 0.90 and context ≥ 0.90). Context alone is capped below accept | **W** (more rigorous); ours has a human-in-loop W lacks |
| **Applying a fix** | by token id, `ocr_txt` kept, reversible, letters-only idempotency | verified unique character span (sentence unique on page, token unique in sentence, Arabic token boundaries) | both safe. Ours is id-based, W is string-span-based |
| **Final text audit** | none | `FinalContextReviewer` (gemini-3.1-pro), 5 numeric validators, refuses rather than guesses. **Off by default** | W |
| **Diacritization** | Local CPU BiLSTM `arabic-diacritizer`, free, only on pages with `diac_ratio < 0.15`. Abandons a page on word-count mismatch | **One Gemini vision call per word crop**. Reads only the marks actually printed. `_check_preservation()` fails the stage if any letter, punctuation or word order changes | W more faithful to the print; ours ~free. **W's letters-unchanged guard is worth porting regardless** |
| **TTS** | Gemini TTS (voice Kore) or Hume (native timestamps) | Gemini TTS, **two narrators** (Leda female / Orus male), 700-char sentence-aware chunking, duration sanity check with one regenerate. **`gemini_tts.py` is 0 bytes in every commit in this repo** | ours works; W's chunker + dual voice are useful ideas |
| **Word timing / sync** | `speak.bind` binds Hume timestamps or faster-whisper alignment to token ids. 78% anchored across 3,873 tokens | **None.** No timing code in any `walidapro/**/*.py`. Its own test expects a `JSON` artifact that nothing produces | **Ours** (W cannot do Job B) |
| **Reader** | `reader.html` with image + text views and karaoke highlight | none (per-page text + WAV files) | **Ours** |
| **TTS budget guard** | provider budget in `service/` (parks pages) | skips TTS for a page with ≥ 50% diacritics `REVIEW` | both; different levels |

---

## 4. Data model

| | Ours | walidapro |
|---|---|---|
| Unit | `work/<book>/pNNN.json` page record | `<out>/page_NNN/` directory |
| Word identity | **stable `p007w042` id**. Box, raw, txt, timing and fixes all hang off it | none. Words are strings in `corrected_text.md`, located by span |
| Audit trail | `fixes[]` in the record; review JSON files | `corrections.jsonl` of `SuspiciousWord`, 25+ fields per word (every score that fed the decision) |
| Status | `extracted → diacritised → spoken` | `page_status.json` per page; `book_report.json` |
| Outputs | page JSON, page JPEG, audio, reader.html | `original_page.png`, `corrected_text.md`, `final_corrected_text.txt`, `final_audio.wav` ×2, crops, `report.json` |

**Consequence for merging:** walidapro's verifier has to become **token-id aware**. It must
accept our tokens and return per-token verdicts. Otherwise its corrections can't be bound
to boxes and timings (constitution II).

---

## 5. Cost and speed per page

| | Ours | walidapro |
|---|---|---|
| Paid calls / page | 1 (text layer + TTS) to 2 (OCR + TTS); +1–2 per flagged page with `tools/` | ~40–200: OCR 1 + reviewer 1 + per suspect (2–5 LightOn + 1 Gemini) + **1 per word for diacritics** + TTS chunks ×2 voices |
| Words / page (measured over 186 pages) | — | median 35, p90 69, max 137, so the diacritics loop alone is 35–137 calls |
| Parallelism | `service` worker fans out pages | per-word diacritics loop is strictly serial (`word_diacritics.py`) |
| Free-tier fit | already hits 10–20 req/day caps | not viable on free tier. Needs paid quota and a measured $/book before it's enabled |

---

## 6. Engineering quality

| | Ours | walidapro |
|---|---|---|
| Per-page failure isolation | `service/`: page ledger, exactly one terminal callback per page (INVARIANT-1). `src/pipeline` drivers still `break` on the first error ❌ | `run_full_pipeline.py`: per-page try/except → `error.txt` + `page_status.json`, loop continues ✅. `ocr_verifier/pipeline.py` buries failures as `<!-- PAGE FAILED -->` inside the text ❌ |
| Resume | status state machine | fingerprinted caches at every expensive call (stronger: config changes invalidate correctly) |
| Error handling | 15-code taxonomy, redacted reasons | re-raises programming errors (`NameError`/`AttributeError`/`TypeError`) instead of treating them as "unreadable image". Good rule |
| Writes | plain writes | `atomic_write_text` (tmp + replace) |
| Config | pydantic-settings, refuses `.env` on disk in service | frozen dataclass, `load_dotenv`; `REVIEWER_MODEL` required |
| Retries | one shared policy with jitter and wall-clock budget | per module; `FinalContextReviewer` can bill up to 9 completions per page (3 app × 2+ SDK) |
| Comments | English | bilingual (Arabic reasoning + English mechanics) |

---

## 7. The two API layers (duplicate work)

Both implement the same V1 Backend contract.

| | `service/ai_engine` (ours) | `walidapro/api` |
|---|---|---|
| Endpoints | only `POST /api/jobs` + health/ready/metrics/ops | V1 endpoint **plus** `/api/v1/pdf`, `/ocr/verify`, `/diacritization`, `/tts`, and `review`/`pipeline` routers that are empty files |
| Auth | RS256 JWKS, rotation, `azp` allowlist, fails closed | RS256 JWKS, issuer/expiry. `.env.example` ships `AI_ENGINE_AUTH_DISABLED=true` |
| Idempotency | atomic insert-if-absent + payload hash → 409 on mismatch | `threading.Lock` + SQLite insert. Duplicate payloads are not compared |
| Execution | separate worker process, leases, durable queue | in-process thread pool (`processing.submit`). **`processing.py` is 0 bytes** |
| Metadata gate / outbox / reconciler | yes / yes / yes | callbacks sent inline; no outbox, no reconciler |
| Timing JSON artifact | yes (`timing_builder`) | test expects it; no code produces it |
| CORS | none needed (server-to-server) | `allow_origins=["*"]` |
| Tests | contract fake, 42-case fault matrix | `test_ai_contract.py` (377 lines, good scenarios) but uncollectable |

Walidapro's `test_ai_contract.py` scenarios are worth replaying against `service/` as an
independent check of the contract.

---

## 8. Current state of `walidapro/` (verified 2026-10-20)

0-byte files that are not package markers:

| File | Effect |
|---|---|
| `ocr_verifier/tts/gemini_tts.py` | `tts/pipeline.py` import fails → Stage 5 and `run_full_pipeline.py` dead. **Empty in both repo commits.** walidapro's docs say it was 19,649 bytes "in HEAD", but that refers to the author's own repo, not this one |
| `api/routers/review.py`, `api/routers/pipeline.py` | `api/main.py` `include_router` → AttributeError, API does not start |
| `api/ai_contract/processing.py` | every accepted job fails |
| `api/jobs.py` | not imported here, but the traceback in `api_output/jobs/b5cf…/page_001/error.txt` comes from `C:\Users\Waleed\…\api\jobs.py` line 148. **The author has a working copy that was never committed** |
| `tests/conftest.py` | fixtures missing, so the suite cannot run |

→ **Before porting anything, get the complete working tree from Waleed.** Reconstructing
these files from the docs is possible, but it means guessing at code that already exists.

---

## 9. What to make: the merge

| Keep from **ours** (default / fast tier) | Port from **walidapro** (verified tier + hardening) | Drop |
|---|---|---|
| text-layer fast path + `--force-ocr` | ContextReviewer (suspect detection) | `walidapro/api/*` (use `service/`) |
| stable token ids, page record, `speak.bind` timings, reader | PaddleOCR word localization (boxes only) | `walidapro/run_full_pipeline.py` orchestrator |
| `service/` job engine, auth, outbox, budget, Docker, CI | crop variants + adaptive LightOn re-reads | walidapro TTS (once chunker + dual voice are ported) |
| `tools/` human-gated correlated-error review | **blind** Gemini crop verifier + `decision.decide` | `api_output/` runtime data (gitignore) |
| local diacritizer for the fast tier | verified-span apply logic → re-expressed on token ids | |
| | **`_check_preservation` letters-unchanged guard → apply to our `tashkel.py` too** | |
| | per-word visual diacritics (verified tier only, parallelized) | |
| | FinalContextReviewer (opt-in) | |
| | fingerprinted caches, atomic writes, re-raise programming errors | |
| | sentence-aware TTS chunker, dual narrator option | |

**Before the verified tier can be turned on** (ADR-009 reversal conditions):

1. Q-07 answered: acceptance is measured on AI accuracy.
2. The per-word diacritics loop is parallelized.
3. A measured $/book exists and is approved.
4. The ported code has tests.

Step-by-step tasks: `specs/004-verified-tier-walidapro/tasks.md`.

## 10. Where each part went (2026-10-22)

The repo is one codebase again. `walidapro/` was removed; its last state is the local git tag
`walidapro-final` (`git show walidapro-final:walidapro/ocr_verifier/decision.py`). Nothing was
imported or wrapped: the parts worth keeping were rewritten in our architecture, with tests.

| walidapro part | Now | Spec |
|---|---|---|
| `decision.py` `decide()`, `utils.normalize_ar` | `service/ai_engine/domain/verify_rules.py` (`decide`, `normalize`, `similarity`) | 022 |
| `visual_reviewer.read_crop` gates (0.72 / one word / 0.12 gap / 0.82 support) | `verify_rules.gate_blind_read`; the call itself is `adapters/ai/verifier/providers.py` `GeminiBlindReader` | 022 |
| `reviewer.py` (`find_suspicious`, `compare_candidate`, `validate_replacement`) | `GeminiReviewer`; filtering in `verify_rules.filter_suspects` | 022 |
| `lighton_ocr.verify_crop_variants` early stop, `_select_best_visual_reading` | `LightOnCropReader`; `verify_rules.enough_votes`, `visual_consensus` | 022 |
| `cropper.save_crops` variants | `adapters/ai/verifier/crops.py` | 022 |
| Unique-span replacement | `verify_rules.resolve_token`: one stable token id or nothing | 022 |
| `pipeline.process_page` | `adapters/ai/verifier/visual.py` `VisualVerifier` (a `PageVerifier`) | 022 |
| `_check_preservation` (letters never change) | `domain/preservation.py`, `src/pipeline/preservation.py` | 006 |
| `SuspiciousWord` audit fields | `domain/evidence.py`, `src/pipeline/evidence.py` | 018 |
| Re-raise programming errors | `domain/errors.py` `PROGRAMMING_ERRORS` | 007 |
| `final_context_reviewer.py` (5 safety checks, `clean_ocr_noise`, prompts) | `domain/final_review_rules.py` + `adapters/ai/verifier/final_review.py`; suggestions only, never applied | 024 |
| `tests/test_ai_contract.py` scenarios | `service/tests/behaviour/test_walidapro_scenarios.py` | 030 |

Not carried over, deliberately: the second API (`api/`), the second orchestrator
(`run_full_pipeline.py`), the module-level config singleton, the import-time `load_dotenv()`, and
the PaddleOCR word locator (the verifier's locator is injectable; it uses OCR token boxes for now).

Still to rewrite from the tag, if wanted: per-word visual diacritics (023), TTS chunking and the
second narrator (032).

