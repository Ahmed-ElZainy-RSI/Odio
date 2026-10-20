# Feature Specification: Don't auto-apply a VLM answer neither engine read

**Feature:** `017-vlm-third-answer-routing` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P1 (a correctness hole in the current tools)

## Problem

`tools/vlm_arbiter.py` returns a verdict per flagged word. 84% matched one of the two engine
reads, but 7% were a **third answer** that neither LightOn nor Gemini produced. Some were
right (removing Gemini's TOC dot leaders). Some were wrong (an ISBN digit string re-guessed
differently from both). Today `apply_corrections.py` treats a verified third answer like any
other verdict. A model inventing text is exactly the case constitution III says must not
change letters on its own.

## User Story 1 — Third answers go to human review (P1)

**Given** a VLM verdict whose letter-normalized text equals neither engine's read, **When**
`apply_corrections.py` runs, **Then** it is **not** applied. It is added to the human review
file (the same `context_decisions.json` flow, seeded `"reject"`) with both engine reads, the
VLM read and the page image link.

**Acceptance:**
1. Verdict == LightOn or == Gemini (letters only) → applied as today.
2. Verdict ≠ both → not applied, appears in review HTML with source `vlm-third-answer`.
3. Verdict differs from both **only by punctuation/noise removal** (e.g. dot leaders) → [NEEDS CLARIFICATION: auto-apply as a noise cleanup?] Proposal: auto-apply only if the letters equal one engine's letters after removing non-letters.

## Requirements

- **FR-001** Classification `match-lighton | match-gemini | noise-only | third-answer` added to each arbiter verdict.
- **FR-002** `apply_corrections.py` applies only the first three.
- **FR-003** `build_context_review.py` renders third answers in their own section.
- **FR-004** A counter in the run summary.
- **FR-005** *(bug found 2026-10-20 by the 006 guard)* A verdict whose text has leading or trailing whitespace, or that contains internal whitespace, is never written into a token as-is. `tools/apply_corrections.py:89` (`tok["txt"] = r["verified_txt"]`) wrote `'يَصْرُخُ؟! '` (with a trailing space) into `فلفل المغرور-lighton` `p010w014`. Strip it. If whitespace remains inside, it's a multi-word answer for one token, so route it to review.

## Success Criteria

- **SC-001** Re-run on the pilot book: every third answer is routed to review, 0 applied.
