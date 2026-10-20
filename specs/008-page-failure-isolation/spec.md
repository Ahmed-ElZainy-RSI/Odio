# Feature Specification: One bad page doesn't stop the book (CLI pipeline)

**Feature:** `008-page-failure-isolation` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P1

## Problem

The `src/pipeline` drivers stop every remaining page on the first exception:
`run_ocr_gemini.py:59-62` (`break`), `run_ocr_lighton.py:58-61` (`break`),
`run_speak_gemini.py:81-84` (`return`), `run_speak_hume.py:62-65` (`return`). That is right for
a 429 (quota is gone, so stop), but wrong for a page-specific failure such as a malformed
response, a bad page image or a bind crash. Those leave later pages unprocessed until someone
notices. `service/` already isolates pages; walidapro's orchestrator does too (`error.txt` +
`page_status.json`).

## User Story 1 — Page-specific failure is recorded and skipped (P1)

**Given** page 7 raises a non-quota error, **When** the driver runs, **Then** page 7's record
gets `status: "failed"`, `error: {stage, type, message, at}`, and pages 8..N are processed.

## User Story 2 — Quota exhaustion still stops the run (P1)

**Given** a 429 / `RESOURCE_EXHAUSTED`, **Then** the driver stops, as today, so it doesn't
burn more quota on failures. The page is **not** marked failed, so the next run retries it.

## User Story 3 — Failed pages are retried on re-run (P2)

**Given** a page with `status: "failed"`, **When** the stage is re-run, **Then** the page is
retried (it does not count as done). The run summary lists failed pages.

## Requirements

- **FR-001** Shared helper `classify_failure(exc) -> "quota" | "page" | "bug"`. `bug` = programming errors, which re-raise (007).
- **FR-002** `failed` is a valid status in the page record schema (009), with an `error` object.
- **FR-003** Resume checks treat `failed` as "not done" for the stage that failed.
- **FR-004** Each driver prints `done / skipped / failed / stopped-on-quota` counts.
- **FR-005** `build_reader.py` shows failed pages as image-only or a placeholder and doesn't crash.

## Success Criteria

- **SC-001** Test: a fake OCR that raises on page 2 of 4 → pages 1, 3, 4 extracted, page 2 `failed`.
- **SC-002** Test: a fake 429 on page 2 → run stops, page 2 has no `failed` status.
