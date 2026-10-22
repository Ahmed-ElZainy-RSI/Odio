# Feature Specification: Verified OCR (walidapro's engine on our token ids)

**Feature:** `022-verified-ocr` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-22 (T017-T019 open) · **Epic:** 004
**Priority:** P1 within the epic. **Depends on:** 005, 018, 020, 021

## Problem

Our fast tier has no in-service OCR correction, and `tools/` correction needs a second OCR
engine plus a human. walidapro has the strongest automated verifier in the repo, but it works
on strings (verified character spans), not on our token ids, and it isn't wired anywhere that
can run.

## User Story 1 — Suspicious words are verified from the image, blind (P1)

**Given** an OCR'd page on the verified tier, **Then**:
1. The reviewer LLM lists suspicious words with optional candidates (JSON repaired locally; a failed review is never "0 suspects").
2. PaddleOCR locates each suspect (boxes only, never text).
3. 5 crop variants; LightOn re-reads with early stop (2–5 calls), and the stopping rule never sees the candidate.
4. Gemini Vision reads the word crop **blind** (crop only). `UNCLEAR` (confidence < 0.72, not a single Arabic word, or equidistant) is zero evidence.
5. `decide()` fuses the scores → ACCEPT / REVIEW / REJECT with walidapro's thresholds unchanged.
6. ACCEPT changes exactly that token's `text`. The original is kept and evidence (018) is recorded.

**Acceptance:**
1. Context 0.99 + weak visual → not ACCEPT.
2. Suspect not resolvable to exactly one token id (repeated word, no box match) → REVIEW.
3. `حسن` never matches inside `المحسن`.
4. Diacritics-only candidate → not an OCR correction (handled by 023).
5. Vision request body contains no original, candidate or sentence (tested).

## Requirements

- **FR-001** `WalidaproPageVerifier` implements `PageVerifier` (020).
- **FR-002** Modules ported: `reviewer.py`, `bbox_detector.py`, `cropper.py`, the LightOn re-read from `lighton_ocr.py`, `visual_reviewer.py`, `decision.py`.
- **FR-003** Suspect → token id resolution replaces `_locate_unique_word_span`: match on letter-normalized text plus the PaddleOCR box overlapping the token's box when present. Exactly one match, or REVIEW.
- **FR-004** All sub-calls are cached (021) and charged to the provider budget.
- **FR-005** Settings: `VERIFIER_REVIEWER_MODEL`, `VERIFIER_VISION_MODEL`, `LIGHTON_BASE_URL`, thresholds (walidapro defaults: suspicion 0.75, accept 0.88, review 0.70).
- **FR-006** PaddleOCR runs in-process, loaded once per worker.

## Success Criteria

- **SC-001** Ported `decide()` passes a table test copied from walidapro's behaviour.
- **SC-002** On reference books (019): **0 false letter edits**, and caught errors > fast tier.
- **SC-003** Calls per page measured and reported (025).
