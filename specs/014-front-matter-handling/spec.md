# Feature Specification: Consistent front-matter handling

**Feature:** `014-front-matter-handling` · **Created:** 2026-10-20 · **Status:** Draft. **Needs a product decision**
**Priority:** P3

## Problem

The two TTS drivers disagree:
- `run_speak_hume.py:25` skips **page 1** unconditionally (`status: "skipped-front-matter"`).
- `run_speak_gemini.py` narrates page 1 (it only skips 0-token pages).
- `service/` (Job B) narrates whatever page the Backend asks for.

Page 1 is assumed to be title / ISBN / deposit number, but that isn't always the case, and
front matter isn't always only page 1. Digits-only pages also defeat the binder (0 anchors).

## Decision needed

[NEEDS CLARIFICATION — product: should title pages, ISBN / deposit pages, and back covers be
narrated?] Options: (a) never, (b) title only, (c) everything. Recommendation: (b). Narrate the
title line and skip pages that are mostly digits, ISBN or publisher boilerplate.

## User Story 1 — Same rule in every engine (P3)

**Given** a page classified as front matter, **Then** every TTS path (Gemini, Hume, service)
applies the same rule, and the record says why (`status: "skipped-front-matter"`,
`skip_reason`).

## Requirements

- **FR-001** Classifier `is_front_matter(tokens, page, page_count)` from content signals: ISBN pattern, `رقم الإيداع`, digit ratio > 0.4, copyright markers. Not from page number alone.
- **FR-002** Used by `run_speak_gemini.py`, `run_speak_hume.py`, and the service Job B (configurable: `SKIP_FRONT_MATTER`).
- **FR-003** The reader still shows skipped pages, unhighlighted.

## Success Criteria

- **SC-001** On the 36 sample books, the classifier matches a hand-labelled set (record precision/recall).
