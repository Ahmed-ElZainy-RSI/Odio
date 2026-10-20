# Feature Specification: Fingerprinted cache for paid calls + atomic writes

**Feature:** `021-provider-call-cache` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P2

## Problem

Our resume model is status-based: a page is redone or skipped as a whole. walidapro caches
**every** expensive call, with a key that covers everything that can change the result
(OCR: PDF SHA-256, DPI, model, `max_side`). A config change invalidates correctly, and a
crashed page re-run pays only for calls that didn't finish. For the verified tier (dozens of
calls per page) this is the difference between a retry costing 1 call and costing 200. walidapro
also writes atomically (`utils.atomic_write_text`: tmp + replace), so a crash never leaves a
half-written file the cache later trusts. Our CLI writers do not.

## User Story 1 — Re-running a page never repays for a finished call (P2)

**Given** a page whose run crashed after 30 of 60 calls, **When** it is retried with the same
config, **Then** 30 calls are served from cache. **Given** the model or DPI changes, **Then**
nothing is served from the old entries.

## User Story 2 — No half-written files (P2)

Every page record, cache entry and artifact is written atomically.

## Requirements

- **FR-001** `ProviderCache` port: `get(key) / put(key, value)`; key = SHA-256 of a canonical JSON of `{provider, model, operation, input_hash, params}`.
- **FR-002** Adapters: filesystem (CLI + single host) and job-store table (service, shared).
- **FR-003** Only **validated** results are cached (as walidapro does for diacritics), never errors.
- **FR-004** `atomic_write_text/bytes` helper used by `src/pipeline` record writes (with 009) and the filesystem cache.
- **FR-005** TTL / size cap configurable; a cache hit is a metric.

## Success Criteria

- **SC-001** Test: changing any key field → miss; same fields → hit.
- **SC-002** Kill-during-write test leaves either the old or the new file, never a partial one.
