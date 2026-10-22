# Feature Specification: Replay walidapro's contract tests against our service

**Feature:** `030-contract-replay-walidapro-tests` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-22 (Q-17, Q-18 for the Backend) · **Epic:** 004
**Priority:** P2. **Depends on:** 005 (for `conftest.py`)

## Problem

Two people implemented the same Backend contract independently. `walidapro/tests/test_ai_contract.py`
(377 lines) encodes Waleed's reading of the spec: auth, token caching, idempotency,
download-URL refresh, 3-step upload, status callbacks. Wherever his reading and ours differ,
one of us misread the spec. Running his scenarios against `service/` finds those differences
cheaply, before the Backend team does.

## User Story 1 — Every walidapro scenario runs against `service/` (P2)

**Given** each test in `test_ai_contract.py`, **Then** an equivalent test exists in
`service/tests/behaviour/test_walidapro_scenarios.py` using our contract fake. Each one either
passes, or has a written disposition (ours right / his right / spec ambiguous → open
question).

## Known differences to resolve

| Topic | walidapro | service |
|---|---|---|
| Duplicate with a different payload | 202 duplicate | 409 |
| `jobId` format | any string (`"job-123"`) | RFC 4122 GUID |
| `folderId` / `pathPrefix` | optional in tests | required |
| Unknown `jobType` | 400 | 422 |
| Audit job without `pageNumber` | 400 | 422 |

## Requirements

- **FR-001** One mapped test per walidapro scenario.
- **FR-002** `differences.md` in this folder: each difference, which side follows the spec PDF (`assets/Backend & AI integration V1.pdf`), and the action taken.
- **FR-003** Spec-ambiguous items added to `docs/backend-v1/22-open-questions.md`.

## Success Criteria

- **SC-001** 100% of scenarios mapped; every difference has a disposition.
