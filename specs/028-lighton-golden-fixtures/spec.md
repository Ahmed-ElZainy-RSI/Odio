# Feature Specification: Golden fixtures from the real corpus for every OCR adapter

**Feature:** `028-lighton-golden-fixtures` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P3 (roadmap `T-6.4` remainder)

## Problem

The text-layer adapter is asserted byte-identical against `work/النجوم/p003.json`. The LightOn
adapter's output is asserted only against a transport stub, so a regression in markdown
cleaning (HTML/LaTeX stripping, which has already broken once on non-deterministic output)
would pass.

## User Story 1 — Recorded provider responses replay through the adapter (P3)

**Given** recorded raw LightOn responses (including the LaTeX `$\mathcal{S}$` variant of page
10), **Then** the adapter output equals the recorded page record (`work/فلفل المغرور-lighton/p003.json`
and others) token for token.

## Requirements

- **FR-001** `service/tests/fixtures/lighton/<book>/pNNN.response.json` + `.expected.json`.
- **FR-002** Same for Gemini OCR (forced-schema response → line boxes).
- **FR-003** A record script that captures new fixtures from a real call (manual, paid, not in CI).

## Success Criteria

- **SC-001** At least 3 LightOn and 3 Gemini fixtures, including one HTML-leak and one LaTeX-leak case.
