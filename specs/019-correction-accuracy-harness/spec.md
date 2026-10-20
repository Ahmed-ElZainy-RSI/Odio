# Feature Specification: Measure correction accuracy against a reference

**Feature:** `019-correction-accuracy-harness` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P1 (prerequisite for 026 and Q-07)

## Problem

The README's sweep table marks the Correction and Tashkeel harnesses "❌ to build". Without
them nobody can say whether a correction method helps, and the fast-vs-verified decision
(026) has no evidence.

## User Story 1 — Score any pipeline output against a hand transcript (P1)

**Given** a reference transcript (`references/<book>/pNNN.txt`) and a book's page records,
**When** `python3 tools/score_corrections.py <work_dir> <reference_dir>` runs, **Then** it
reports per page and in total:
- WER and CER on letters (diacritics folded)
- errors **caught** (wrong in raw OCR, right after correction)
- **false edits** (right in raw OCR, wrong after correction)
- errors **missed**
- diacritic error rate (DER), where the reference is diacritized

## User Story 2 — Reference set exists (P1)

Reference transcripts for: Ibn Battuta in Egypt (5 pages, 538 words, already hand-read),
the 10 known substitutions in `الشمس` (from README), and 1 undiacritized book.

## Requirements

- **FR-001** Alignment on letter-normalized tokens (difflib, as `ensemble_review.py` does).
- **FR-002** Uses `ocr_txt` (pre-correction) vs `txt` (post) from page records to classify caught / false edit / missed.
- **FR-003** JSON + Markdown output, stable so runs can be diffed.

## Success Criteria

- **SC-001** Harness reproduces the README's 10 known `الشمس` errors as "raw errors" when run on the raw OCR.
- **SC-002** Runs in < 5 s per book, offline.
