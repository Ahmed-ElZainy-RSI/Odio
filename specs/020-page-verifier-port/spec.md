# Feature Specification: PageVerifier port and quality-tier routing

**Feature:** `020-page-verifier-port` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P1 (must exist before any verified-tier logic)

## Problem

ADR-009 and `service/docs/07-status-and-roadmap.md` say "the `PageVerifier` port exists so
enabling the tier later is configuration". **It does not exist.** `service/ai_engine/ports/ai.py`
has `PageRenderer`, `OcrEngine`, `Diacritizer`, `TtsEngine` and `Aligner` only. `qualityTier` is
accepted, stored and downgraded (`application/accept_job.py:140`), but no page task reads it.

## User Story 1 — The tier reaches the page task (P1)

**Given** a job stored with `quality_tier = verified` and `ENABLE_VERIFIED_TIER=true`, **When**
a page task runs, **Then** it calls `PageVerifier.verify(page_image, tokens)` after OCR and
before diacritization. With `fast`, it doesn't.

## User Story 2 — A no-op verifier proves the plumbing (P1)

`PassThroughVerifier` returns the tokens unchanged with all-`KEEP` verdicts, so the routing
can ship and be tested before any real verifier exists.

## Requirements

- **FR-001** Port `PageVerifier.verify(image: bytes, tokens: list[Token], *, page: int) -> VerificationResult` with `tokens` (possibly changed `text`), `verdicts: list[Verdict]` (`token_id`, `status`, `evidence`), `calls_made: dict[provider,int]`.
- **FR-002** Contract: never changes token ids or count. Only `text` of tokens whose verdict is `ACCEPT` may change.
- **FR-003** Page task enforces FR-002 after the call and fails the page on violation.
- **FR-004** Text-layer pages skip verification.
- **FR-005** Verdicts persisted with the page (artifact ledger or a page-side table) for audit.

## Success Criteria

- **SC-001** Unit test: a verifier that renumbers ids → page fails with a contract-violation reason.
- **SC-002** e2e: verified job with the pass-through verifier produces byte-identical artifacts to fast.
