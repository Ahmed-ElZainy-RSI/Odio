# Feature Specification: Fast vs verified benchmark (decides Q-07)

**Feature:** `026-tier-benchmark` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P1 at the end of the epic. **Depends on:** 019, 022, 023, 025

## Problem

ADR-009 keeps the verified tier off until four conditions hold: Q-07 answered, diacritics
parallelized, measured $/book approved, ported code tested. Q-07 can't be answered without
numbers.

## User Story 1 — One report the product owner can decide from (P1)

**Given** the reference books (019), **When** the benchmark runs both tiers (and `tools/`
correction as a third column), **Then** one report shows per tier: WER, CER, caught errors,
**false edits**, DER, calls/page, $/page, $/1,000 books, wall-clock/page p50/p90, % pages
needing human review.

## Requirements

- **FR-001** `tools/benchmark_tiers.py` runs: fast (service adapters, in-process), verified, CLI + tools/ chain.
- **FR-002** Paid run requires an explicit `--budget-usd` cap and stops at it.
- **FR-003** Output `specs/026-tier-benchmark/results/<date>.md` + JSON.
- **FR-004** Includes a recommendation section filled in by a person, not generated.

## Success Criteria

- **SC-001** Report published. Q-07 answered and recorded in `docs/backend-v1/22-open-questions.md`.
- **SC-002** ADR-009 updated (confirmed or reversed).
