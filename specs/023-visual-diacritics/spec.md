# Feature Specification: Visual per-word diacritics, parallelized (verified tier)

**Feature:** `023-visual-diacritics` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P2. **Depends on:** 006, 020, 021, 022 (boxes)

## Problem

Our diacritizer predicts tashkeel from context. On books printed **with** tashkeel, the right
answer is what's printed, not what the model predicts. walidapro reads the marks from
each word crop (`word_diacritics.py`) and applies them only if the letters are unchanged. But
it is **one vision call per word**, strictly serial (`word_diacritics.py:1611-1660`): 35 words
median, 137 max, 30 s to 7 min per page.

## User Story 1 — Printed marks, read from the page (P2)

**Given** a verified-tier page, **Then** each word crop is read for its marks. A result is used
only if the letters match the reference token (006) and alignment ≥ 0.72. Otherwise the token
is `REVIEW` and keeps its current text. Existing OCR marks are not trusted just because they
exist.

## User Story 2 — Bounded concurrency (P1 within this feature)

Word calls run concurrently (default 8, configurable), and the result order is by token id.
Page wall-clock p90 ≤ [NEEDS CLARIFICATION: target, proposal 60 s].

## User Story 3 — Too many REVIEWs → don't narrate (P2)

**Given** ≥ 50% of a page's words in `REVIEW`, **Then** TTS is skipped for that page and the
page is reported with reason `DIACRITICS_REVIEW_REQUIRED` (walidapro's budget guard).

## Requirements

- **FR-001** Implements `Diacritizer` port for the verified tier; the fast tier keeps `LocalDiacritizer` (016).
- **FR-002** Each word call cached (021) and budgeted.
- **FR-003** Skip pages with `diac_ratio < 0.15`? [NEEDS CLARIFICATION: on undiacritized books there are no printed marks to read, so visual diacritics are pointless there. Proposal: verified tier uses visual on `keep`-routed pages and local on `diacritise`-routed pages.]

## Success Criteria

- **SC-001** Concurrency test: shuffled completion order → identical output.
- **SC-002** DER on a diacritized reference book (019) lower than the local model's.
