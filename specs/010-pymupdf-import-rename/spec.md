# Feature Specification: Move off the deprecated `fitz` import

**Feature:** `010-pymupdf-import-rename` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P3 (maintenance)

## Problem

PyMuPDF has deprecated the `fitz` alias and warns on import. A future release removes it,
and then every PDF path breaks at once. Users: `service/ai_engine/adapters/ai/renderer_pymupdf.py`,
`service/ai_engine/adapters/ai/ocr_textlayer.py`, and the `src/pipeline` modules
(`main.py`, `extract.py`, `pages.py`, OCR drivers).

## User Story 1 — No deprecated import anywhere (P3)

**Acceptance:** `grep -rn "import fitz" src service tools` returns nothing. All test suites
pass. The service's `filterwarnings = error::DeprecationWarning:ai_engine.*` stays on.

## Requirements

- **FR-001** `import pymupdf` everywhere (same API).
- **FR-002** Minimum PyMuPDF version that provides `pymupdf` pinned in `service/pyproject.toml` (`pymupdf>=1.24.3`) and `requirements.txt`.

## Success Criteria

- **SC-001** Zero `fitz` imports; both suites green.
