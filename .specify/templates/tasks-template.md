# Tasks: [FEATURE]

**Input:** `specs/[###-feature-name]/` (plan.md, spec.md, data-model.md, contracts/)

Format: `- [ ] T### [P] [US#] description — path`
- `[P]` = can run in parallel (different files, no dependency)
- `[US#]` = user story it serves
- `[x]` = done (with evidence: test name / commit / file)

## Phase 1 — Setup

- [ ] T001 ...

## Phase 2 — Foundational (blocks all stories)

- [ ] T010 ...

## Phase 3 — User Story 1 (P1)

Tests first (they must fail before implementation):
- [ ] T020 [P] [US1] ...

Implementation:
- [ ] T025 [US1] ...

**Checkpoint:** US1 independently testable.

## Phase N — Polish & cross-cutting

- [ ] T090 ...

## Dependencies

[ordering notes]
