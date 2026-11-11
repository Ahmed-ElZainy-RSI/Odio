# Feature Specification: OCR the «قيمي» books with Gemini-3.8-Flash into `result/`, one folder per book

**Feature:** `035-qiyami-books-ocr` · **Created:** 2026-11-11 · **Status:** Implemented 2026-11-11 · code in `tools/qiyami/`
**Priority:** P2

## Problem

The «قيمي» series (الأمانة, الأنانية, التعاون, العند, …) are printed **with tashkeel** (diac ratio
~0.75 on story pages). The main pipeline's job for such books is to read the marks exactly, not
to add them. A tuning run (`tools/qiyami/tune_flash.py`) found the Gemini-3.8-Flash
setup that scores best on the hand-checked pages of الأمانة (pages 4 and 5: letters 100%, words
100%, tashkeel ≈ 96%). الأمانة was then read whole with that setup. الأنانية and التعاون had to
be read the **same way**, each book on its own, so the three can be compared and shown together.

## User Story 1 — One folder per book (P2)

**Given** a book PDF in `books/`, **When** I run the script on it, **Then** `result/<book>/` holds
the rendered pages, one JSON per page, a book summary and the whole book as text, and nothing of
another book is touched.

## User Story 2 — Same setup for every book (P2)

**Given** two books read on different days, **When** I compare them, **Then** both were read with
the same model, prompt, resolution, zoom strips, samples and vote, and each JSON says so.

## User Story 3 — See the page and its text (P2)

**Given** the results, **When** I open the viewer, **Then** I see each page image beside its OCR text,
per book or the three books together, offline.

## Requirements

- **FR-001** Setup, fixed: model `gemini-3.8-flash`, prompt `v2`, `media_resolution=high`,
  `thinking=low`, the full page **plus 3 horizontal strips enlarged ×2** (8% overlap),
  **5 samples** (temperature 0.4) merged by a **per-letter majority vote on the marks**;
  page rendered from the PDF at **200 dpi**.
- **FR-002** Output per book, in `result/<book>/`:
  `images/pNNN.png` (what the model saw) · `json/pNNN.json` (text, lines, tokens `pNNNwNNN`,
  `diac_ratio`, setup, `pdf_sha256_16`, `score`) · `book.json` (id = PDF hash, per-page summary) ·
  `<book>_كامل.txt` (whole book, `===== صفحة N =====` between pages).
- **FR-003** The folder name is the book's title, not the file name (`--book`): `التعاون (1).pdf`
  → `result/التعاون/`.
- **FR-004** Raw model answers stay in `work/qiyami/<book>/page_N/sample_*.raw` (git-ignored);
  `--rebuild` rebuilds `result/` from them with no API call.
- **FR-005** `score` is filled only where a reference text exists (الأمانة pages 4–5, `score_ocr.GT`);
  elsewhere it is `null`, never a placeholder.
- **FR-006** Viewers: `result/<book>/<book>.html` per book, and `result/كتب_قيمي.html` for the three
  books together (tabs, page rail, image beside text, copy, font size). Data is embedded, so the
  page works opened from disk; images are read from `result/<book>/images/`.

## Success criteria

- **SC-001** الأنانية: 26/26 pages, التعاون: 25/25 pages, no page skipped.
- **SC-002** The text in `result/كتب_قيمي.html` equals `json/pNNN.json` `text` for every page of the
  three books (checked against the published page «كتب قيمي: الصفحة والنص»: 0 differences).

## Out of scope

Correcting the OCR, adding tashkeel, TTS and word timings (that is `001-book-pipeline`).
`result/` is a deliverable, not an input of the pipeline.
