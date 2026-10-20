# Feature Specification: OCR Correction (two-engine ensemble + human gate)

**Feature:** `002-ocr-correction` · **Created:** 2026-10-20 · **Status:** Implemented (retro-spec)
**Code:** `tools/` · **Source docs:** `README.md §Correcting OCR errors`, `docs/ocr-correction-architecture-built.html`, `docs/tools-reference.html`, `TESTING.md`

## User Scenarios & Testing

### User Story 1 — Catch errors where engines disagree (P1)

An operator OCRs a scanned book with both LightOn and Gemini. Words where the two disagree
are flagged; a vision model reads the real page image once per flagged page and the verified
answer is written back into the page record, reversibly.

**Acceptance:**
1. **Given** `work/<book>` (Gemini) and `work/<book>-lighton`, **When** `ensemble_review.py` runs, **Then** `ensemble_review.json` lists disagreements per page (letters compared with diacritics folded).
2. **When** `vlm_arbiter.py` runs, **Then** each flagged page gets one vision call, not one per word.
3. **When** `apply_corrections.py` runs, **Then** only VERIFIED corrections are written; `ocr_txt` keeps the original; re-running changes nothing (idempotent, letters-only comparison).

### User Story 2 — Replace a page LightOn failed on (P2)

**Given** a page where ≥ 50% of words are `gemini-only` (e.g. white text on dark background),
**When** `replace_page.py` runs, **Then** the whole page's tokens are swapped for Gemini's.

### User Story 3 — Catch correlated errors both engines agree on (P1)

Both engines can agree on the same wrong valid word (`الْغِنَاءُ` vs real `الْغَنَّاءِ`).
`context_detector.py` (text-only, OpenRouter/DeepSeek so a Gemini quota day doesn't block it)
proposes suspects; `build_context_review.py` renders an HTML review sorted by suspicion; a
human edits `context_decisions.json`; only accepted suggestions are applied.

**Acceptance:** every decision is seeded `"reject"`; nothing from this layer auto-applies.

### User Story 4 — One command up to the manual step (P2)

`tools/run_full_pipeline.py <pdf> [--pages N|A:B]` runs both OCR passes → ensemble →
arbiter → apply → context detection → review HTML, then stops and says what to do by hand.
Re-running it is idempotent.

### Edge cases

- VLM returns a third answer neither engine produced (7% of verdicts; e.g. an ISBN digit) → must not auto-trust.
- VLM returns undiacritized text for an already-diacritized correct token → compare letters only, keep marks.
- 429 mid-run → resume at the failed page.
- LightOn non-determinism between calls on the same page.

## Requirements

- **FR-001** Align two engines' token lists (difflib) per page; classify `agree`, `lighton-only`, `gemini-only`, `differ`.
- **FR-002** Arbitrate flagged pages with one vision call per page against the rendered image.
- **FR-003** Apply word-level fixes keeping token ids and `ocr_txt`; record `fixes[]`.
- **FR-004** Whole-page replacement when `gemini-only ≥ 50%`.
- **FR-005** Correlated-error detection with a provider independent of the OCR provider.
- **FR-006** Human-in-the-loop gate: decisions file, default reject, apply only accepted.
- **FR-007** After any applied fix, the page's downstream status is reset so it is re-diacritized and re-spoken (only that page).
- **FR-008** Orchestrator stops before the manual step; never automates it.

## Success Criteria

- **SC-001** On the pilot book: 234 flags / 17 pages caught (measured); 84% of VLM verdicts matched one of the two reads.
- **SC-002** Context detector found 109 and 29 correlated suspects on two books; 0 auto-applied.
- **SC-003** Re-running any tool on an already-processed book changes 0 tokens.

## Out of scope

- In-process, per-word visual verification with blind crops — that is walidapro's design, see `004`.
