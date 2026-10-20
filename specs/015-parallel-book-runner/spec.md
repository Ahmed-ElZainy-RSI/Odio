# Feature Specification: Run many books in parallel (CLI)

**Feature:** `015-parallel-book-runner` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P3 (the service already parallelizes; this is for offline batch production)

## Problem

`main.py` processes one book, serially. At N = 1,000 the README estimates 14 h of wall-clock
at 1 req/s for TTS alone. Free-tier quotas are per project per model, so naive parallelism
just hits 429 sooner.

## User Story 1 — Batch a folder of PDFs (P3)

**Given** `assets/PDF Samples/`, **When** `python3 src/pipeline/batch.py <dir> --workers 4`
runs, **Then** books are processed concurrently and each book stays resumable. A summary
table lists, per book: pages done / failed / quota-stopped.

## User Story 2 — Respect provider rate limits globally (P3)

**Given** per-provider limits (e.g. `GEMINI_TTS=10/day`, `GEMINI_FLASH=20/day`, or paid RPM),
**Then** all workers share one limiter. Hitting the limit pauses that provider's stages and
the other stages keep going.

## Requirements

- **FR-001** Process pool across books (not pages within a book, to keep per-book status writes single-writer).
- **FR-002** Shared rate limiter (file lock + sliding-window counter in `work/.ratelimit.sqlite`).
- **FR-003** Graceful stop on Ctrl-C: finish in-flight pages.
- **FR-004** Text-layer books (free extraction) are scheduled first, so they reach TTS early.

## Success Criteria

- **SC-001** 10 text-layer books with `--workers 4` ≥ 3× faster than serial, with a fake TTS.
- **SC-002** 0 provider-limit violations under a configured limit.

## Out of scope

Production scaling, which belongs to `service/` (worker fan-out + provider budget).
