# Feature Specification: Detect a text layer that belongs to the wrong page

**Feature:** `012-text-layer-trust-check` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P2

## Problem

`main.py:has_text_layer` trusts any PDF with more than 50 words. On `شكرا يامى` the text layer was
real, well-spelled Arabic about a different page (ghost text). No word-count or
diacritic-ratio check can catch that, so today it is only caught by a human who knows to pass
`--force-ocr`.

## User Story 1 — Ghost text layers are caught automatically (P2)

**Given** a PDF with a text layer, **When** the pipeline starts, **Then** it OCRs a small
sample of pages (default 2: first page with ≥ 20 words, and the middle page) and compares the
OCR text with the text layer. Below the similarity threshold, the whole book switches to the
OCR path and the reason is logged.

**Acceptance:**
1. `شكرا يامى.pdf` → switches to OCR with reason `text-layer-mismatch (sim=0.xx)`.
2. `النجوم.pdf` → stays on the text layer (similarity ≥ threshold).
3. `--force-ocr` still skips the check; `--trust-text-layer` skips it the other way.

## User Story 2 — Mixed PDFs per page (P3)

[NEEDS CLARIFICATION: switch per page (like walidapro's `MIXED_PDF`) or per book? Per page
needs per-page source tracking, which the record already has (`source`).] Proposal: per book
for v1, per page later.

## Requirements

- **FR-001** Similarity = token-level overlap on letters only (diacritics, tatweel and alef forms folded, reusing `ensemble_review.normalize_for_compare`).
- **FR-002** Threshold configurable (`TEXT_LAYER_MIN_SIMILARITY`, default 0.6, calibrated on the 36 sample books).
- **FR-003** Cost: at most 2 OCR calls per text-layer book, and the result is cached by PDF SHA-256.
- **FR-004** Also port walidapro's per-page rules (`pdf_type_detector.py`: `visible_chars < 15`, `< 3 words` ⇒ needs OCR) for near-empty pages.

## Success Criteria

- **SC-001** Across the 36 sample books: 0 false switches on known-good text layers; `شكرا يامى` caught.
- **SC-002** Added cost ≤ 2 calls per text-layer book.
