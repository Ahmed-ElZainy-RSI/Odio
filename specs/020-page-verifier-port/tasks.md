# Tasks: PageVerifier port

- [x] T001 [P] Contract tests — `service/tests/unit/test_page_verifier_contract.py` (10): an accepted correction is clean; a text change without ACCEPT (or with no verdict), a dropped token, renumbering, reordering, a verdict for an unknown token, and two verdicts for one token are all violations
- [x] T002 [P] Routing tests — `service/tests/unit/test_verified_tier_routing.py` (9): a verified job runs the verifier and ships its tokens; a fast job never calls it; text-layer pages are skipped; verified with no verifier falls back to fast; a contract break fails the page as `INTERNAL_ERROR`; pass-through verified output == fast output, byte for byte; the worker wires a verifier only when `ENABLE_VERIFIED_TIER` is on
- [x] T003 `PageVerifier` Protocol in `ports/ai.py`; result types and the pure `contract_violations()` in new `domain/verification.py` (`Verdict` wraps 018's `Evidence`; `VerificationResult` carries `calls_made` per provider for 025)
- [x] T004 `adapters/ai/verifier_passthrough.py`
- [x] T005 Page task: `_verify()` runs after OCR, before the Text artifact is built, only for `quality_tier == VERIFIED`, a wired verifier, and a non-text-layer engine; a violation raises `VerifierContractViolation` (`INTERNAL_ERROR`)
- [ ] T006 Persist verdicts (FR-005). **Moved to 022.** The pass-through produces no verdicts, and the storage shape (table vs. artifact) should follow what the real verifier emits. For now each verified page logs `page.verified` with flagged/accepted/needs_review/calls
- [x] T007 Docs: the status doc's "the `PageVerifier` port exists so enabling it later is configuration" is now true; ADR-009 needs no change

Double gate, as built: `qualityTier: verified` is downgraded at accept unless `ENABLE_VERIFIED_TIER` is on (existing), **and** the worker only wires a verifier while the flag is on (new). Turning the flag off therefore also stops verification for jobs accepted earlier.

Verification (2026-10-21, vx2lm): service 691 passed including slow (was 672); `src/tests` 147; `ruff` clean.
Branch: `feature/020-page-verifier-port`, merged into `dev`.
