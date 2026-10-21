# Feature Specification: Measured cost per page and per book

**Feature:** `025-cost-metering` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-21 (CLI + service); validation against a paid run pending
**Priority:** P1 (ADR-009 reversal condition #3)

## Problem

There is no cost instrumentation anywhere. The README's "$38 / 36 books" is a projection. The
provider budget (`T-5.7`) counts calls for rate limiting but not money, and doesn't
distinguish input/output tokens, audio seconds or image calls. Choosing fast vs verified, or
an OCR vendor, needs real numbers.

## User Story 1 — Every paid call is recorded with its cost drivers (P1)

**Given** any provider call (service or CLI), **Then** a usage row is recorded:
`provider, model, operation, job_id/book, page, input_tokens, output_tokens, images, audio_ms, chars, cached (bool), at`.

## User Story 2 — Cost report per book and per tier (P1)

`GET /ops/cost?jobId=` (service) and `python3 tools/cost_report.py <work_dir>` (CLI) report
calls and estimated $ per page, per book and per stage, using a price table.

## Requirements

- **FR-001** Price table `config/prices.yaml` (per provider/model/unit, with `as_of` date and source URL). Prices are config, not code.
- **FR-002** Usage extracted from provider responses (Gemini `usage_metadata`, OpenAI-compatible `usage`, LightOn per page, TTS chars/audio ms).
- **FR-003** Cache hits (021) recorded at $0, so savings are visible.
- **FR-004** Metric `ai_engine_cost_usd_total{provider,model,tier}`.

## Success Criteria

- **SC-001** Fast-tier book of 16 pages: report matches a hand count of calls.
- **SC-002** Report exists for at least one verified-tier run before Q-07 is decided (026).
