# Feature Specification: Every correction carries its evidence

**Feature:** `018-correction-evidence-record` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P2

## Problem

Our `fixes[]` entry is `{id, from, to, by}`. It says what changed, but not why, so a fix can't
be audited or challenged later. walidapro records 25+ fields per word in `SuspiciousWord`
(`walidapro/ocr_verifier/models.py`): every score that fed the decision. When the verified
tier lands (022), both paths should write the same evidence shape so audits compare like
with like.

## User Story 1 — Audit any fix from the record alone (P2)

**Given** any applied fix, **Then** its record contains the inputs and decision: engine reads,
VLM/blind read and confidence, context score, decision rule, status, timestamp, tool version.

## User Story 2 — One evidence shape for both tiers (P2)

`tools/` fixes and verified-tier fixes use the same `Evidence` schema. Unused fields are null.

## Requirements

- **FR-001** `Evidence` schema in `specs/018-correction-evidence-record/contracts/evidence.schema.json`.
- **FR-002** Fields: `token_id, original, candidate, applied, status (ACCEPT|REVIEW|REJECT), detector (ensemble|context|reviewer), reads {lighton, gemini, vlm, blind, variants[]}, scores {context, visual, final}, rule, bbox, crop_ref, model_versions, at`.
- **FR-003** `fixes[]` entries embed `evidence` (or reference `evidence/<token_id>.json` when large).
- **FR-004** `build_review.py` shows the evidence on hover.

## Success Criteria

- **SC-001** Every fix written after this feature validates against the schema.
- **SC-002** walidapro `SuspiciousWord` maps 1:1 onto `Evidence` (mapping table in plan.md).
