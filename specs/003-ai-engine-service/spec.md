# Feature Specification: AI Engine Service (Backend V1 contract)

**Feature:** `003-ai-engine-service` · **Created:** 2026-10-20 · **Status:** Implemented, Postgres + verified tier pending
**Code:** `service/ai_engine/` · **Source docs:** `assets/Backend & AI integration V1.pdf`, `docs/backend-v1/*`, `service/docs/*`

## User Scenarios & Testing

### User Story 1 — Job A: pages image + text extraction (P1)

The Backend posts `PAGES_IMG_AND_TEXT_EXTRACTION` for a PDF. The engine accepts immediately
(202), downloads the source, reports `pagesCount`, then for every page uploads an `Image`
and a `Text` artifact via the 3-step upload and reports that page completed or failed.

**Acceptance:**
1. **Given** a valid job, **Then** `PATCH /api/jobs/{id}/metadata {pagesCount}` is delivered **before** any page work starts.
2. **Then** every page 1..N receives exactly one terminal callback (completed **or** failed) — INVARIANT-1.
3. **Given** the presigned URL expires (403), **Then** a fresh one is fetched via `GET /api/files/{fileId}/get-download-url`.

### User Story 2 — Job B: page audio + word timing (P1)

The Backend posts `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` with `pageNumber`. The engine
fetches the page text, re-derives token ids, synthesizes audio, aligns timings, uploads an
`Audio` and a timing `JSON` artifact (with `anchor_ratio`), and reports completion.

### User Story 3 — Duplicate and crash safety (P1)

Re-posting the same `jobId` returns 202 `duplicate: true` and does no work; a different
payload under the same id returns 409. A worker crash mid-job resumes without re-uploading
artifacts that were already confirmed.

### User Story 4 — Provider budget (P2)

When a provider's call budget for the window is exhausted, pages are **parked, not failed**,
and resume when the window resets. `GET /ops/budget` shows what is left.

### Edge cases

Unknown fields → 422; non-https / non-allowlisted source host → 422; Job A with `pageNumber` → 422;
Keycloak 401 → refresh once; Backend 5xx → retry with jitter honoring `Retry-After`; job past
`JOB_DEADLINE_SECONDS` → reconciler fails stragglers.

## Requirements

- **FR-001** Exactly one inbound endpoint `POST /api/jobs` (+ `/health`, `/ready`, `/metrics`, `/ops/budget`, debug `GET /api/jobs/{id}`).
- **FR-002** Inbound JWT: RS256, JWKS with rotation, `iss`/`exp`/`nbf`, `azp` allowlist, fail closed (enforced when `AUTH_ENFORCE=true`).
- **FR-003** Idempotent accept = atomic insert-if-absent; payload hash comparison.
- **FR-004** Durable job store, page ledger with leases, artifact ledger (row only after confirm-upload), callback outbox, reconciler.
- **FR-005** OCR engine `auto` resolves per document, prefers text layer; Gemini / LightOn otherwise (wraps `001`).
- **FR-006** Timing artifact reuses `speak.bind` verbatim.
- **FR-007** `qualityTier: fast|verified` (proposal); `verified` downgraded to `fast` unless `ENABLE_VERIFIED_TIER=true` → `004`.
- **FR-008** API layer never imports adapters/SDKs/legacy pipelines (tested).

## Success Criteria

- **SC-001** INVARIANT-1 holds across the 42-case fault matrix.
- **SC-002** Full test suite with zero network/paid calls (status doc: 616 tests).
- **SC-003** A real 16-page book completes against the fake Backend in ~3 s, and again under injected faults.

## Open questions (from `docs/backend-v1/22-open-questions.md`)

- **Q-01** Auth: will the Backend send tokens / which `azp`? (gates `AUTH_ENFORCE` default)
- **Q-07** Is V1 acceptance protocol conformance or AI accuracy? (gates the verified tier, `004`)
- **Q-10** No `artifactType` for bounding boxes in the V1 contract.
