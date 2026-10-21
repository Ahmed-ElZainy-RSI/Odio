# Feature Specification: Books identified by content, not filename

**Feature:** `013-content-based-book-id` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-21
**Priority:** P3

## Problem

Book identity is `pdf_path.stem.strip()` (`src/pipeline/main.py`, `tools/run_full_pipeline.py:52`).
Directory names contain Arabic, spaces, `?` and `' - Copy'`. Renaming a PDF orphans its work.
Two copies of the same PDF are processed twice, and the README records a misfiled book
(`sam_2/الشمس.txt` is actually `السماء.pdf`).

## User Story 1 — Same PDF, same book, whatever its name (P3)

**Given** a PDF, **Then** its book id is the first 12 hex chars of its SHA-256. The
human-readable title is kept as metadata. Renaming the file resumes the same work directory.

## User Story 2 — Existing work directories keep working (P3)

A migration writes `work/<title>/book.json` with `{id, sha256, title, source_path}`. It also
creates `work/by-id/<id>` → `work/<title>` symlinks, so nothing existing moves.

## Requirements

- **FR-001** `book.json` written on first run.
- **FR-002** Lookup order: by SHA-256 (index `work/index.json`), then by title (legacy).
- **FR-003** Duplicate content under a different filename → reuse and log `duplicate-of`.
- **FR-004** The service is unaffected (it is keyed by Backend `fileId` / `jobId`).

## Success Criteria

- **SC-001** Rename a processed PDF and re-run: 0 API calls.
- **SC-002** Migration over existing `work/` is idempotent.
