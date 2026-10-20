# Feature Specification: Retire walidapro's duplicated layers

**Feature:** `033-retire-walidapro-duplicates` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P3 (last). **Depends on:** 022, 023, 024, 030, 032

## Problem

After the port, `walidapro/` still contains a second API (`api/`), a second orchestrator
(`run_full_pipeline.py`, 2,911 lines), a second TTS stack, and docs describing them. Two
implementations of the same contract drift, and new contributors don't know which is real.

## User Story 1 — One implementation of each thing (P3)

**Given** the port is complete, **Then**:
- `walidapro/api/`, `run_api.py`, `run_full_pipeline.py`, `ocr_verifier/tts/` are removed (kept in git history and tagged `walidapro-final`).
- `walidapro/README.md` becomes a pointer: where each module now lives (table).
- `docs/backend-v1/01-current-architecture.md` §2, ADR-009 and `docs/pipeline-vs-walidapro-report.md` are updated to the post-merge state.

## Requirements

- **FR-001** Tag `walidapro-final` before deletion.
- **FR-002** Module location table in `walidapro/README.md`.
- **FR-003** No import of `walidapro` remains anywhere (grep test).
- **FR-004** Agreed with Waleed before deletion.

## Success Criteria

- **SC-001** `grep -rn walidapro service src tools` returns only docs.
