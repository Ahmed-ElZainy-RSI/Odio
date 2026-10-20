# Data Model: Book Pipeline

One JSON per page is the only source of truth. Audio and the reader derive from it.

## Page record — `work/<book>/pNNN.json`

| Field | Type | Set by | Meaning |
|---|---|---|---|
| `page` | int (1-based) | extract/OCR | page number |
| `source` | `"pdf-text-layer"` \| `"ocr"` | extract/OCR | where text came from |
| `ocr_engine` | string | OCR | e.g. `gemini-3.6-flash`, `lighton/parse-v3` |
| `box_level` | `"word"` \| `"line"` \| `"none"` | extract/OCR | precision of `box`; reader hides image view on `none` |
| `diac_ratio` | float | extract/OCR | diacritics per Arabic letter |
| `path` | `"keep"` \| `"diacritise"` | router | `keep` if `diac_ratio ≥ 0.15` |
| `status` | enum (below) | each stage | resume state |
| `tokens` | Token[] | all | the anchored words |
| `fixes` | Fix[] | `002` tools | reviewable diff log |
| `audio` | path/URL | TTS | page audio |
| `tts`, `align` | string | TTS | provenance (`gemini`/`hume`, `native`/`whisper`) |
| `bind_report` | object | `speak.bind` | `tokens`, `anchored`, `interpolated`, `untimed` |

## Token

| Field | Type | Meaning |
|---|---|---|
| `id` | `p{page:03d}w{index:03d}` | the anchor — never renumbered |
| `raw` | string | what extraction/OCR saw |
| `txt` | string | corrected + diacritized reading text |
| `ocr_txt` | string? | pre-correction text, kept so a fix is reversible (`002`) |
| `box` | `[x0,y0,x1,y1]` \| null | page-image coords |
| `t` | `[start_ms, end_ms]` \| null | audio window |
| `t_src` | `"anchor"` \| `"interp"` \| null | how `t` was obtained |

## Status state machine

```
(none) ──extract/OCR──► extracted ──tashkel──► diacritised ──TTS+bind──► spoken
                           │                                    ▲
                           └──── path == keep ─────────────────┘
```

Resume rule (constitution V): a stage skips a page whose status is **its output state or
any later state**. Bug history: `run_tashkel.py` once checked only `diacritised` and reset
13 `spoken` pages; `run_ocr_*` once checked only `extracted` and would re-OCR corrected pages.

## Fix (from `002`)

`{ "id", "from", "to", "by": "vlm"|"context"|"page-replace", "votes"? }`
