# Feature Specification: Page records are validated against a schema

**Feature:** `009-page-record-schema-validation` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-20 (branch `dev`)
**Priority:** P2

## Problem

The page JSON is the single source of truth, but nothing checks its shape. Status values
have already drifted: the drivers write `skipped-front-matter` (`run_speak_hume.py:27`) and
`skipped-empty` (`run_speak_gemini.py:33`), which the documented state machine doesn't know
about. A typo in a status silently breaks resume, which is how the past resume bugs happened.

## User Story 1 — A malformed record fails loudly in tests and in dev (P2)

**Given** any writer of `pNNN.json`, **When** it writes, **Then** the record is validated
against `specs/001-book-pipeline/contracts/page-record.schema.json`. In tests an invalid
record raises. In production runs it logs a warning and still writes, so a run is never lost
to validation.

## User Story 2 — Existing corpus is audited (P2)

A one-off command validates every record under `work/` and lists violations by type.

## Requirements

- **FR-001** Schema covers every status actually written: `extracted`, `diacritised`, `spoken`, `skipped-empty`, `skipped-front-matter`, plus the optional `diac_error` object (006). There is no `failed` status: 008 records failures in `failures.json` instead.
- **FR-002** A single `write_record(path, rec)` helper used by every writer (`src/pipeline` + `tools/`).
- **FR-003** Validation mode via env `PAGE_RECORD_VALIDATION=strict|warn|off` (tests: strict, default: warn).
- **FR-004** Token id pattern `^p\d{3}w\d{3}$`; `txt` has no whitespace (same rule as `service/ai_engine/domain/tokens.py`).

## Success Criteria

- **SC-001** All writers go through `write_record` (enforced by a grep test: no `json.dumps(rec` outside the helper).
- **SC-002** Audit over the existing corpus is run and its result recorded.
