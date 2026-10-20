# Feature Specification: Explicit redirect policy on source download

**Feature:** `029-source-fetch-redirect-policy` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P2 (security)

## Problem

The source fetcher (`service/ai_engine/adapters/storage/source_fetcher.py`) validates the
URL it was given (https, host allowlist, SSRF checks). A `302` to a private address is a
separate attack path. Today it is safe only because `httpx` doesn't follow redirects by
default: "safe by accident rather than by decision" (status doc). Nothing pins it.

## User Story 1 — Redirects are refused or re-validated (P2)

**Given** the presigned URL responds `3xx`, **Then** the fetch fails with a non-retryable
`SOURCE_REDIRECT_REFUSED`. [NEEDS CLARIFICATION: does the storage provider ever redirect
legitimately (e.g. S3 region redirects)? If so: follow at most 1 redirect and re-run the full
URL validation on the target.]

## Requirements

- **FR-001** `follow_redirects=False` set explicitly on every source request.
- **FR-002** 3xx handled per the decision above.
- **FR-003** Tests: redirect to `http://169.254.169.254/` and to `10.0.0.1` are refused.

## Success Criteria

- **SC-001** Tests pass. A future httpx default change can't silently re-enable redirects.
