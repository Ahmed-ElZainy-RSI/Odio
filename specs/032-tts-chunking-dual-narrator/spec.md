# Feature Specification: Sentence-aware TTS chunking and optional second narrator

**Feature:** `032-tts-chunking-dual-narrator` · **Created:** 2026-10-20 · **Status:** Draft · **Epic:** 004
**Priority:** P3. **Depends on:** 005 (needs `gemini_tts.py` recovered to compare)

> **Source after consolidation (2026-10-22):** `walidapro/` was removed (spec 033). Its code is at the
> local git tag `walidapro-final`. Read it for the rules; rewrite natively; never import it.

## Problem

We synthesize one request per page. Long pages risk truncation or quality drop, and a failed
request means repaying for the whole page. walidapro chunks at sentence boundaries
(`. ! ؟ ? : ؛`, max 700 chars, commas, then words as fallback), validates each chunk's
duration (regenerating once), merges WAVs after checking format, and produces **two narrators**
(female Leda, male Orus).

## User Story 1 — Long pages are synthesized in chunks without breaking timings (P3)

**Given** a page longer than `TTS_MAX_CHARS` (default 700), **Then** it is chunked at sentence
boundaries. Each chunk is synthesized and duration-checked, then merged. Word timings are
aligned on the merged audio (or per chunk and offset), and token ids are unchanged.

**Acceptance:** chunked and unchunked synthesis of the same page give the same token-id → timing
coverage (anchored + interpolated) within ±5%.

## User Story 2 — Second narrator (P3, product decision)

[NEEDS CLARIFICATION — product: is a male/female choice a requirement? Doubles TTS cost.]
If yes: `voice` per job (the Job B payload has no voice field, so this is an open question for
the Backend, like walidapro's `AI_ENGINE_DEFAULT_NARRATOR`).

## Requirements

- **FR-001** Port `text_chunker.py` (sentence-aware; `clean_text` whitespace-only).
- **FR-002** Port `audio_manager.py` merge with format validation.
- **FR-003** Chunk-level cache (021) and duration gate (already in `GeminiTts._check_duration_is_sane`).
- **FR-004** Timing offsets: chunk start = sum of previous chunk durations.

## Success Criteria

- **SC-001** A 2,000-char synthetic page → 3+ chunks, monotonic timings, all ids covered.
