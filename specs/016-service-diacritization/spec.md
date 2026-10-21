# Feature Specification: Diacritization in the AI Engine service (fast tier)

**Feature:** `016-service-diacritization` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-21 (opt-in: `DIACRITIZER=local`)
**Priority:** P1

## Problem

`service/ai_engine/ports/ai.py` defines a `Diacritizer` port, but **no adapter implements it
and no use case calls it**. Job A's `Text` artifact is therefore OCR or text-layer text
as-is. On undiacritized books (ratio ≈ 0.0, about half the corpus) Job B then synthesizes
undiacritized text, and Arabic TTS guesses the vowels. The CLI pipeline already handles this
with `tashkel.py` + the 0.15 router.

## User Story 1 — Job A text is diacritized when the page needs it (P1)

**Given** a page with `diac_ratio < 0.15`, **When** Job A builds its `Text` artifact, **Then**
the text is diacritized by the local CPU model, token ids are unchanged, and the letter
preservation guard (006) passes. Pages ≥ 0.15 are left as printed.

**Acceptance:**
1. Word-count mismatch → page text left undiacritized, page still completed, and a `diacritization: "skipped"` note in logs/metrics.
2. Preservation failure → same as 1.
3. Job B re-tokenizing Job A's text gets identical token ids (existing round-trip test still passes).

## Requirements

- **FR-001** `LocalDiacritizer` adapter wrapping `src/pipeline/tashkel.py` through `adapters/ai/legacy.py`, with no module-global model (one instance per worker, injected).
- **FR-002** Router threshold `DIACRITIZE_BELOW_RATIO` (default 0.15).
- **FR-003** Metric `ai_engine_diacritization_total{outcome=applied|kept|mismatch|preservation_failed}`.
- **FR-004** Worker image includes the model weights (build arg, like whisper).

## Success Criteria

- **SC-001** e2e: an undiacritized fixture page → Text artifact diacritized; a diacritized fixture page → unchanged bytes.
- **SC-002** CPU time per page < 1 s (as measured for the CLI).
