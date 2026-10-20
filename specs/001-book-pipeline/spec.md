# Feature Specification: Book Pipeline (PDF → read-along reader)

**Feature:** `001-book-pipeline` · **Created:** 2026-10-20 · **Status:** Implemented (retro-spec)
**Code:** `src/pipeline/` · **Source docs:** `README.md` (§Pipeline, §Data contract, §Two extraction paths)

## User Scenarios & Testing

### User Story 1 — Turn one PDF into a reader (P1)

A content operator runs one command on a book PDF and gets `work/<book>/reader.html`: the
page image and reflowed Arabic text, with audio that highlights each word as it is spoken.

**Independent test:** `python3 src/pipeline/main.py "assets/PDF Samples/النجوم.pdf"` produces a
reader whose highlight follows the audio.

**Acceptance scenarios:**
1. **Given** a PDF with an embedded text layer (> 50 words), **When** the pipeline runs,
   **Then** text and per-word boxes come from the text layer with zero OCR calls.
2. **Given** a scanned PDF, **When** it runs with `--ocr gemini`, **Then** pages are OCR'd with
   line boxes (`box_level: "line"`); with `--ocr lighton`, text only (`box_level: "none"`) and
   the reader drops the image view.
3. **Given** a page with diacritic ratio < 0.15, **Then** it is routed `diacritise`; ≥ 0.15
   routed `keep` and left untouched.
4. **Given** synthesized audio, **Then** every token gets `t` (ms) + `t_src`
   (`anchor` / `interp`) or is left untimed and rendered unhighlightable.

### User Story 2 — Resume after a quota stall (P1)

Free-tier providers cap at 10–20 requests/day. Re-running the same command continues from
the first unfinished page of each stage without redoing or undoing finished work.

**Acceptance:** re-running after a 429 makes zero calls for pages already at or past a stage
(status recognised: `extracted` → `diacritised` → `spoken`).

### User Story 3 — Force OCR on a lying text layer (P2)

**Given** a PDF whose text layer is real Arabic but belongs to a different page
(found on `شكرا يامى`), **When** run with `--force-ocr`, **Then** OCR reads the pixels instead.

### Edge cases

- Diacritizer returns a different word count than given → page left un-diacritized and reported.
- Hume returns fewer timestamps than tokens, or an inverted interval → anchor what exists, interpolate the rest.
- Back cover / ISBN / digits-only pages → zero anchors; reader must not crash.
- LightOn returns HTML/LaTeX artifacts non-deterministically → stripped generically.
- Front matter (title/ISBN page) → currently skipped for narration.

## Requirements

- **FR-001** Detect text layer per document (`> 50` words) and prefer it unless `--force-ocr`.
- **FR-002** Assign stable token ids `p{page:03d}w{index:03d}` at extraction; never renumber.
- **FR-003** Clean text-layer words by rule (kashida, stray marks, dropped hamza) keeping `raw`.
- **FR-004** Route tashkeel by `diac_ratio` (threshold 0.15).
- **FR-005** Diacritize with a local CPU model (`arabic-diacritizer`), re-attaching by position.
- **FR-006** Synthesize per page with Gemini TTS (default, voice `Kore`) or Hume.
- **FR-007** Bind word timings to token ids: Hume native timestamps, or faster-whisper forced alignment for Gemini.
- **FR-008** Emit one page record JSON per page (see `contracts/page-record.schema.json`).
- **FR-009** Build a standalone `reader.html` that reads only page records + audio.
- **FR-010** `--pages` scopes extraction only; later stages catch up any backlog on disk.
- **FR-011** Every stage resumable; resume checks recognise every later status.

### Key entities

- **Book** — identified today by PDF filename stem → `work/<book>/`.
- **Page record** — `pNNN.json`; `status`, `path`, `diac_ratio`, `source`, `box_level`, `tokens[]`.
- **Token** — `id`, `raw`, `txt`, `box`, `t`, `t_src`.

## Success Criteria

- **SC-001** Text-layer book: 0 paid OCR calls; 1 paid call per page (TTS).
- **SC-002** Timing: ≥ 90% anchored on Hume native (measured 93.7% on النجوم); Gemini+whisper measured 51% anchored + 48% interpolated — accepted as model limit, exposed per page.
- **SC-003** A re-run after interruption wastes 0 calls on completed pages.
- **SC-004** No page-count mismatch silently mis-binds a diacritic to the wrong token id.

## Assumptions & Out of scope

- Correction of OCR errors is feature `002-ocr-correction`.
- Service/HTTP delivery is feature `003-ai-engine-service`.
- Audio is not stored in git (object storage / CDN at scale).
