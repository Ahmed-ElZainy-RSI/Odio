# Feature Specification: Review every proposed change in its sentence, and decide in the page

**Feature:** `034-review-in-context` · **Created:** 2026-11-02 · **Status:** Implemented 2026-11-02 · **Epic:** 002 (correction chain)
**Priority:** P2

## Problem

The correction chain proposes word changes from two places, and a person checks them:

- `ensemble_review.py` + `vlm_arbiter.py`: the engines disagree, Gemini reads the page image,
  and `apply_corrections.py` applies the verdict **automatically**;
- `context_detector.py`: an LLM flags words both engines agreed on, as **suggestions only**.

Three gaps make that review slow and incomplete:

1. **No context in the records.** `ensemble_review.json` and `context_review.json` store the
   word and the proposal, never the words around it. A reviewer (or a later audit) cannot see
   which sentence the decision was made in without opening the page.
2. **The review page is read-only.** `context_review.html` shows the current decision, but
   changing one means hand-editing `context_decisions.json`, matching ids by eye.
3. **Image-verified fixes are never reviewed.** They are applied by rule. A person who sees a
   wrong one on the page has no supported way to undo or correct it.

## User Story 1 — See the sentence (P2)

**Given** a flagged word, **When** I open `ensemble_review.json`, `context_review.json` or the
review page, **Then** I see the phrase it sits in: the words before it and after it, up to the
sentence boundary, with the word itself marked.

## User Story 2 — Decide in the page (P2)

**Given** the review page, **When** I pick *Keep OCR word*, *Use proposal* or type my own word
next to an entry and press *Save*, **Then** `context_decisions.json` holds my decision, and
`apply_context_suggestions.py` applies it. Nothing is applied by the page itself.

## User Story 3 — Overrule an automatic fix (P2)

**Given** a word `apply_corrections.py` changed from the image, **When** I choose *Undo fix*
or type my own word, **Then** `apply_context_suggestions.py` puts back the OCR word (or mine)
and records that a person decided it. Leaving it alone keeps the fix.

## Requirements

- **FR-001** Every non-KEEP row of `ensemble_review.json` and every entry of
  `context_review.json` carries `context: {before, after}`: up to 6 words on each side, cut at
  a sentence end (`. ! ? ؟ … ؛`), taken from the LightOn text the detector compared.
- **FR-002** Existing reports gain the field when the tool runs again, **without** recomputing
  or re-billing anything already there (verdicts, suspicions, decisions are untouched).
- **FR-003** One decisions file, one vocabulary, for every kind of entry:
  `"accept"` = the machine's proposal, `"reject"` = the OCR word, any other string = that text.
  Defaults: suggestions `reject` (as today), automatic fixes `accept` (absent = keep the fix).
- **FR-004** The review page shows, per entry: the phrase with the word marked, every reading
  (LightOn, Gemini, image verdict or LLM proposal), the suspicion, and the three controls.
  Automatic fixes are listed in their own group per page.
- **FR-005** Saving: `build_context_review.py --serve` serves the page on `127.0.0.1` only and
  writes decisions with `POST`; it accepts only ids that are on the page, string values, and
  same-origin requests, and writes the file atomically. Opened as a plain file, the page offers
  the same JSON as a download instead.
- **FR-006** Undoing or replacing an automatic fix records evidence `decided_by="human"`
  (`rule` `human-revert` / `human-custom`), keeps `ocr_txt`, and resets the page to `extracted`
  so audio is redone. A token whose text changed since the fix is skipped and reported.

## Success Criteria

- **SC-001** Every reviewable entry in the page and both JSON files shows its phrase (tested).
- **SC-002** A decision made in the page reaches `context_decisions.json` and is applied by
  `apply_context_suggestions.py` (tested end to end through the server).
- **SC-003** 0 paid calls added: context is built from text already on disk.
- **SC-004** The server refuses unknown ids, non-string values and cross-origin posts (tested).

## Out of scope

- Applying from the page: applying stays a command, so a stray click cannot change a book.
- Word boxes on the page image: LightOn has none; the image is shown whole, as today.
- The service's verifier (022/024): its REVIEW verdicts already export in this format
  (`/ops/verdicts?format=review`); feeding them into this page is a later step.
