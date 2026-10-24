# Feature Specification: Opt-in final context review

**Feature:** `024-final-context-review` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-24 as `off | suggest` (export T005 open) · **Epic:** 004
**Priority:** P3

> **Source after consolidation (2026-10-22):** `walidapro/` was removed (spec 033). Its code is at the
> local git tag `walidapro-final`. Read it for the rules; rewrite natively; never import it.

## Problem

After verification, some errors remain that only full-page reading reveals. walidapro's
`FinalContextReviewer` (`final_context_reviewer.py`, 1,092 lines) does a conservative text-only
audit with `gemini-3.1-pro`. Five numeric validators reject unsafe rewrites
(`UnsafeReviewError` → original kept). It is off by default because it has no image.

## User Story 1 — Suggestions only, never silent rewrites (P3)

**Given** `FINAL_REVIEW=suggest`, **Then** the reviewer's proposed edits are recorded as
`REVIEW` verdicts with evidence and never applied automatically. They go to human review,
like `tools/context_detector.py`.

[RESOLVED 2026-10-24 by building `off | suggest` only; `apply` would be a new mode. Original question: should a mode `apply` exist at all? walidapro allows applying when
validators pass. Recommendation: no. Text-only evidence can't authorize letter changes
(constitution III). Keep `off | suggest`.]

## Requirements

- **FR-001** Port the reviewer and its 5 validators. Output is a list of per-token suggestions, not rewritten text.
- **FR-002** Retry budget: one app-level retry, SDK retries disabled (walidapro could bill up to 9 completions per page: risk R-04).
- **FR-003** Off by default; verified tier only.

## Success Criteria

- **SC-001** 0 automatic letter changes from this stage (tested).
- **SC-002** ≤ 2 completions per page (tested with a counting fake).
