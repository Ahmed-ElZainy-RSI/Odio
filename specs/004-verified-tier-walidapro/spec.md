# Feature Specification: Verified Tier (port walidapro's correctness engine)

**Feature:** `004-verified-tier-walidapro` · **Created:** 2026-10-20 · **Status:** In progress: 022 and 033 done 2026-10-22; 023, 024, 032 open
**Input:** `walidapro/ocr_verifier/` (removed; local tag `walidapro-final`), ADR-009, `docs/pipeline-vs-walidapro-report.md`

## User Scenarios & Testing

### User Story 1 — Verified OCR per page, on our token ids (P1)

For a job with `qualityTier: "verified"`, every OCR'd page is checked word by word. Suspicious
words are located, read blind from the image, and corrected only when visual evidence
supports the change. Every decision is recorded against the word's stable token id.

**Independent test:** run the `PageVerifier` adapter on a recorded page with fake providers.
Assert ACCEPT/REVIEW/REJECT for a fixed evidence table, and assert token ids are unchanged.

**Acceptance:**
1. **Given** a suspect with a candidate but an `UNCLEAR` blind read, **Then** the status is `REVIEW` and the letters are unchanged.
2. **Given** context score 0.99 and weak visual consensus, **Then** the status is not `ACCEPT`.
3. **Given** an `ACCEPT`, **Then** exactly that token's `txt` changes, `ocr_txt` keeps the original, and `fixes[]` gains an entry with the full evidence.
4. **Given** a page with no OCR (text layer), **Then** the verifier is skipped (constitution VI).

### User Story 2 — Diacritization never alters letters (P1, both tiers)

**Given** any diacritizer output (our BiLSTM or walidapro's visual per-word), **When** marks
are stripped, **Then** letters, punctuation and word order equal the input. Otherwise the
page fails that stage and keeps its pre-diacritization text.

### User Story 3 — Visual diacritics for the verified tier (P2)

For verified-tier pages, diacritics come from per-word crops, so only the marks printed in the
book are applied. Pages with ≥ 50% of words in `REVIEW` skip TTS and are flagged.

### User Story 4 — Off unless explicitly enabled and budgeted (P1)

With `ENABLE_VERIFIED_TIER=false` (default), a `verified` request is served as `fast`. When
enabled, every paid call is charged to the provider budget, and exhaustion parks the page.

### Edge cases

Proper names (context must never "fix" them); a word repeated in a sentence (ambiguous →
REVIEW); diacritics-only candidates (deferred to visual diacritics, not OCR correction);
PaddleOCR finds no matching box (→ REVIEW); LightOn non-determinism; programming errors inside
a provider call must raise, never become an "unreadable" result.

## Requirements

- **FR-001** `PageVerifier` adapter in `service/ai_engine/adapters/ai/` wrapping walidapro stages 2–4. Input: page image + tokens. Output: per-token verdicts.
- **FR-002** The blind-read protocol: the vision request contains only the crop.
- **FR-003** `decision.decide` thresholds ported unchanged, with their table-driven tests.
- **FR-004** Fixes applied by token id. walidapro's unique-span rule becomes "token resolves to exactly one id".
- **FR-005** Letters-unchanged guard shared by both tiers' diacritization.
- **FR-006** Per-word visual diacritics with bounded concurrency.
- **FR-007** Fingerprinted cache for every paid call (inputs + model + config).
- **FR-008** Per-call cost metering and a per-book cost report.
- **FR-009** Delete or archive `walidapro/api/`, `walidapro/run_full_pipeline.py`, and committed `api_output/`.

## Success Criteria

- **SC-001** On the Ibn Battuta reference transcript (538 words): verified tier has fewer word errors than the fast tier and **0 false letter edits**.
- **SC-002** Measured paid calls/page and $/book published before enabling the tier.
- **SC-003** Verified-tier page wall-clock p90 ≤ [NEEDS CLARIFICATION: target] after parallelizing.
- **SC-004** All ported modules covered by hermetic tests; no paid calls in CI.

## Assumptions

- Waleed supplies the complete working tree (`processing.py`, `gemini_tts.py`, `api/jobs.py`, routers, `conftest.py`) before porting starts.
- PaddleOCR CPU is acceptable in the worker image (adds weight; separate image target if needed).
