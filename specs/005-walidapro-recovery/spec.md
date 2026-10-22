# Feature Specification: Recover walidapro's missing code and baseline its tests

**Feature:** `005-walidapro-recovery` · **Created:** 2026-10-20 · **Status:** Superseded 2026-10-22: the prototype was not recovered but retired; its rules were rewritten natively (022) and the folder removed (033) · **Epic:** 004
**Priority:** P0. Blocks every port from walidapro (020–024, 030, 032, 033)

## Problem

Six walidapro modules are 0 bytes in every commit in this repo. Because of them, the API does
not start, `run_full_pipeline.py` fails at import, TTS is dead, and pytest cannot collect. A
committed traceback (`walidapro/api_output/jobs/b5cf29d077ea41e7/page_001/error.txt`)
points at `C:\Users\Waleed\...\api\jobs.py:148`, so a working copy exists on the author's
machine.

## User Story 1 — Porting starts from real code, not reconstructions (P0)

A developer porting walidapro code has the author's complete working tree and a passing
baseline test run. Behaviour changes after the port can then be told apart from behaviour
that was already there.

**Acceptance:**
1. **Given** the recovered tree, **When** `find walidapro -name '*.py' -size 0` runs, **Then** only the package `__init__.py` markers are listed.
2. **When** `cd walidapro && pip install -r requirements-dev.txt && pytest`, **Then** collection succeeds and the result (pass/fail counts) is recorded in this folder.
3. **Then** `python run_api.py` starts and `GET /health` returns `{"status":"ok"}`.

## User Story 2 — Runtime data is out of git (P1)

`walidapro/api_output/` (SQLite db, uploaded PDFs, job outputs), `__pycache__/`,
`.pytest_cache/`, `.qodo/` are not tracked.

## Requirements

- **FR-001** Obtain from Waleed: `api/ai_contract/processing.py`, `ocr_verifier/tts/gemini_tts.py`, `api/jobs.py`, `api/routers/review.py`, `api/routers/pipeline.py`, `tests/conftest.py`.
- **FR-002** If any file cannot be obtained, reconstruct it from the contracts listed in `walidapro/docs/development.md` § Reconstructing, and mark it `# RECONSTRUCTED` at the top.
- **FR-003** Record the baseline test result in `baseline.md` in this folder.
- **FR-004** Gitignore and untrack walidapro runtime artifacts.

## Success Criteria

- **SC-001** 0 non-marker empty `.py` files.
- **SC-002** walidapro test suite collects; baseline recorded.
- **SC-003** No runtime artifacts tracked under `walidapro/`.

## Out of scope

Fixing walidapro behaviour. This feature only restores it and measures it.
