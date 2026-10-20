# Feature Specification: Programming errors are never treated as provider failures

**Feature:** `007-reraise-programming-errors` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P2

## Problem

A broad `except Exception` around a provider call turns a bug (`NameError`, `AttributeError`,
`TypeError`) into "the provider failed". The bug then gets retried, parked or reported to
the Backend as a page failure, and it looks like an outage. walidapro enforces the rule (its
functional audit, item 2). Our `src/pipeline` drivers catch `Exception` per page, and the
service retry layer classifies failures.

## User Story 1 — A bug surfaces as a bug (P2)

**Given** an adapter raises `TypeError` inside a provider call, **When** the page task runs,
**Then** the error is not retried and is logged as `internal_error` with a stack trace. The page
is still reported failed exactly once (INVARIANT-1 holds), with a reason that says it was an
internal error, not a provider error.

**Acceptance:**
1. `TypeError` in `GeminiTts.synthesize` → 0 retries, failure code `INTERNAL`, not `PROVIDER_*`.
2. `httpx.ConnectError` → retried as today.
3. In `src/pipeline` drivers, `TypeError` aborts the run with a traceback (it isn't printed as a page error and skipped).

## Requirements

- **FR-001** Retry classification (`service/ai_engine/adapters/http/retry.py`) treats `NameError`, `AttributeError`, `TypeError`, `KeyError`, `AssertionError` as non-retryable internal errors.
- **FR-002** `run_page_task` maps them to the internal-error code of the 15-code taxonomy.
- **FR-003** `src/pipeline` per-page handlers (after 008) re-raise them.

## Success Criteria

- **SC-001** Fault-matrix test: injected `TypeError` → 0 provider retries, 1 terminal callback.
