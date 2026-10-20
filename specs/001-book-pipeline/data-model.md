# Data Model: Book Pipeline

One JSON per page is the only source of truth. Audio and the reader derive from it.

The machine-readable contract is [contracts/page-record.schema.json](contracts/page-record.schema.json),
identical to the runtime copy `src/pipeline/page-record.schema.json` that every writer
validates against (spec 009). This page was corrected on 2026-10-20 against 92 real records /
3,919 tokens recovered from git history. The first version followed the README's design
sketch and got `t_src`, `box`, `fixes` and untimed tokens wrong.

## Page record — `work/<book>/pNNN.json`

| Field | Type | Set by | Meaning |
|---|---|---|---|
| `page` | int (1-based) | extract/OCR | page number |
| `source` | `"pdf-text-layer"` \| `"ocr"` | extract/OCR | where text came from |
| `ocr_engine` | string | OCR | `gemini-3.6-flash`, `lighton/parse-v3` (absent on text-layer path) |
| `box_level` | `"line"` \| `"none"` (\| `"word"`) | OCR | precision of `box`. **Absent on the text-layer path**, where boxes are measured per word; the reader then defaults to word |
| `diac_ratio` | float | extract/OCR | diacritics per Arabic letter |
| `path` | `"keep"` \| `"diacritise"` | router | `keep` if `diac_ratio ≥ 0.15` |
| `status` | enum (below) | each stage | resume state |
| `tokens` | Token[] | all | the anchored words |
| `diac_error` | `{id, before, after, reason}` | `run_tashkel` | the diacritiser changed a letter (006); cleared on success |
| `tashkeel_engine` | string | `run_tashkel` | `arabic-diacritizer` |
| `tts`, `voice`, `align` | string | TTS | provenance |
| `audio`, `duration_ms` | string, int | TTS | page audio, relative to the book dir |
| `whisper_words` / `hume_words` | list | TTS | raw timestamps, kept so re-binding needs no API call |
| `bind_report` | `{hume_timestamps, hume_dropped_invalid, tokens, anchored, interpolated, untimed, monotonic}` | `speak.bind` | alignment quality |
| `book`, `source_pdf`, `pdf_sha256_16`, `render_size`, `ocr_engine_replaced_from` | | various | provenance |

Extra fields are allowed.

## Token

| Field | Type | Meaning |
|---|---|---|
| `id` | `p{page:03d}w{index:03d}` | the anchor, never renumbered (grows past 3 digits rather than wrapping) |
| `raw` | string | what extraction/OCR saw |
| `txt` | string, **no whitespace** | corrected + diacritised reading text |
| `fixes` | string[] | what changed this token: `kashida`, `dropped-hamza`, `leading-punct` (clean_word rules), `ensemble-vlm`, `context-llm`, `vlm-reviewed` (corrections) |
| `ocr_txt` | string? | pre-correction text, kept so a correction is reversible |
| `box` | `[x, y, width, height]` | **percentages** of the page; absent when the engine gives no geometry |
| `t` | `[begin_ms, end_ms]` | audio window; **absent** (not null) when untimed |
| `t_src` | `"anchor"` \| `"interpolated"` | present exactly when `t` is |

## Status state machine

```
(none) ──extract/OCR──► extracted ──tashkel──► diacritised ──TTS+bind──► spoken
                           │                                    ▲
                           └──── path == keep ─────────────────┘
TTS drivers may also write: skipped-empty (0 tokens), skipped-front-matter (Hume, page 1)
```

Resume rule (constitution V): a stage skips a page whose status is **its output state or
any later state**. A failed stage never changes status. The failure is recorded in
`work/<book>/failures.json` instead (spec 008), so the stage retries the page next run.

Bug history: `run_tashkel.py` once checked only `diacritised` and reset 13 `spoken` pages;
`run_ocr_*` once checked only `extracted` and would re-OCR corrected pages.

## Files next to the records

| File | Written by | Purpose |
|---|---|---|
| `failures.json` | every driver (008) | page → stage → `{type, message, at}` |
| `ensemble_review.json` | `tools/ensemble_review.py`, `vlm_arbiter.py` | engine diff + VLM verdicts |
| `vlm_review.json` | `tools/apply_corrections.py` (017) | VLM answers neither engine read, awaiting review |
| `context_review.json`, `context_decisions.json` | `tools/context_detector.py` | correlated-error suspects + human decisions |
