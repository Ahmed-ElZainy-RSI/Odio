# Feature Specification: Don't auto-apply a VLM answer neither engine read

**Feature:** `017-vlm-third-answer-routing` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-20 (branch `dev`)
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
`apply_corrections.py` runs, **Then** it is **not** applied. It is queued in
`vlm_review.json` with both engine reads, and seeded `"reject"` in the same
`context_decisions.json` the context-detector review uses.

**Acceptance:**
1. Verdict == LightOn or == Gemini (via `normalize_for_compare`) → applied as today.
2. Verdict ≠ both → not applied; it appears in the review HTML tagged "VLM third answer".
3. *(resolved)* Punctuation/noise-only differences need no separate rule. `normalize_for_compare` keeps only letters and digits, so a verdict that drops dot leaders already **agrees** with an engine, while one that drops or changes a digit (ISBN) does not.

**Why a separate queue file:** `context_detector.py` runs after `apply_corrections.py` and
skips pages already present in `context_review.json` (its resume rule). Writing third answers
there would make it skip those pages. The two sets of ids can't collide: the detector only
checks words both engines agreed on, and third answers only come from words they disagreed on.

## Requirements

- **FR-001** `classify_verdict()` → `match-lighton | match-gemini | multi-word | third-answer`.
- **FR-002** `apply_corrections.py` applies only the matches. `third-answer` and `multi-word` go to `vlm_review.json` (rebuilt every run; existing decisions never overwritten).
- **FR-003** `build_context_review.py` merges `vlm_review.json`, tags those entries, counts them in the header, and sorts them first (suspicion 1.0). `apply_context_suggestions.py` applies accepted ones with fix label `vlm-reviewed`.
- **FR-004** A counter in the run summary.
- **FR-005** *(bug found 2026-10-20 by the 006 guard)* A verdict whose text has leading or trailing whitespace, or that contains internal whitespace, is never written into a token as-is. `tools/apply_corrections.py:89` (`tok["txt"] = r["verified_txt"]`) wrote `'يَصْرُخُ؟! '` (with a trailing space) into `فلفل المغرور-lighton` `p010w014`. Strip it. If whitespace remains inside, it's a multi-word answer for one token, so route it to review.

## Success Criteria

- **SC-001** Re-run on the pilot book: every third answer is routed to review, 0 applied.
