# Architecture

How the pipeline works, stage by stage, and why each guard exists.

- [Stage 1 — PDF type detection](#stage-1--pdf-type-detection)
- [Stage 2 — OCR verification](#stage-2--ocr-verification)
- [The decision engine](#the-decision-engine)
- [Stage 3 — Final context review](#stage-3--final-context-review-opt-in)
- [Stage 4 — Visual diacritization](#stage-4--visual-diacritization)
- [Stage 5 — Dual-voice TTS](#stage-5--dual-voice-tts)
- [Caching](#caching)
- [Data models](#data-models)

---

## Stage 1 — PDF type detection

**Module:** `ocr_verifier/pdf_type_detector.py` · **Endpoint:** `POST /api/v1/pdf/inspect`

Pure local analysis with PyMuPDF — no external API calls, no cost. For each page it
extracts the embedded text layer and counts characters, visible characters, Arabic
characters, words, and images.

Decision rules, in order:

| Condition | Decision | Reason |
|---|---|---|
| `total_chars == 0` | `OCR_REQUIRED` | No text layer found |
| `visible_chars < 15` | `OCR_REQUIRED` | Text layer exists but too little usable text |
| `len(words) < 3` | `OCR_REQUIRED` | Very few words detected |
| otherwise | `TEXT_LAYER` | Usable embedded text found |

The whole document is then classified: all-text ⇒ `TEXT_LAYER`, all-OCR ⇒ `OCR_REQUIRED`,
otherwise `MIXED_PDF`.

Use this to avoid paying for OCR on pages that already carry good text.

---

## Stage 2 — OCR verification

**Module:** `ocr_verifier/pipeline.py` (`OCRVerificationPipeline`) · **Endpoint:** `POST /api/v1/ocr/verify`

This is the core of the system. Per page:

### 2.1 Render

`ocr_verifier/pdf_render.py` renders the page with **pypdfium2** at `dpi` (default 220).
Images are `.copy()`-detached from the PDF bitmap and the document is closed before
returning, so pypdfium2 never emits "objects are still open".

`parse_pages()` accepts `3`, `1-16`, `1,3,5`, `1-4,8-12` and returns sorted, deduplicated,
in-range 0-based indices.

### 2.2 OCR — LightOn

`ocr_verifier/lighton_ocr.py` posts the page as PNG to LightOn's Parse API
(`{LIGHTON_BASE_URL}/api/v3/parse`). Images larger than `LIGHTON_MAX_SIDE` (1540px) are
downscaled with LANCZOS first.

Retries with exponential backoff (`2^attempt` seconds) on HTTP 429 and 5xx, up to
`API_RETRIES` (3). Page markdown fragments are joined in index order. An empty result or a
non-`completed` status raises rather than returning empty text.

### 2.3 Suspicious-word detection — ContextReviewer

`ocr_verifier/reviewer.py` sends the **full page text** to a Gemini text model
(`REVIEWER_MODEL`) and asks for suspicious words with optional correction candidates.

Cost/safety policy in `_chat_json()`:
- maximum 3 attempts
- HTTP/network failures are retried
- malformed JSON is **repaired locally first** (fenced-block stripping, brace extraction)
  before spending another API call
- a failed review is **never** silently converted into "0 suspicious words"

A suspicious word with no plausible candidate stays `REVIEW`; the reviewer does not invent
one.

### 2.4 Localization — PaddleOCR

`ocr_verifier/bbox_detector.py` runs PaddleOCR (`PP-OCRv5` mobile detector +
`arabic_PP-OCRv5_mobile_rec`) purely to find *where* words are.

> PaddleOCR's own recognized text is used **only** for fuzzy matching against the target
> word. It is never treated as OCR output.

`find_best()` normalizes Arabic (strip diacritics and tatweel, unify alef forms and
alef-maqsura), returns immediately on an exact normalized match, otherwise scores with
`difflib.SequenceMatcher` plus a small confidence bonus (`box.score * 0.05`, capped at 1.0).

oneDNN and the PIR API are disabled via env vars at import time to avoid Windows/CPU issues.

### 2.5 Cropping — five image variants

`ocr_verifier/cropper.py` produces, per suspicious word:

- **word crop** — the bbox padded by `CROP_PADDING_X` / `CROP_PADDING_Y` (30 / 20)
- **context crop** — the same bbox padded ×4, for human review
- **five processing variants** of the word crop: `original`, `upscaled`, `contrast`,
  `sharp`, `threshold`

### 2.6 Visual re-read — LightOn, adaptive

`LightOnOCR.verify_crop_variants()` reads the variants in a fixed preferred order and
**stops early**: after at least two variants, if any normalized Arabic reading has two
votes, the remaining LightOn calls are skipped.

Two properties matter here:
- the stopping rule never sees the candidate, so it is **not biased** toward the
  correction we hope for
- worst case is 5 requests; typical case is 2

Programming errors (`NameError`, `AttributeError`, `TypeError`) are re-raised rather than
being swallowed into an empty reading — a bug must not masquerade as "the image was
unreadable".

`_select_best_visual_reading()` then cleans each reading, extracts the closest word,
groups agreeing readings and picks the strongest consensus.

### 2.7 Independent visual verification — Gemini Vision

`ocr_verifier/visual_reviewer.py` (`GeminiVisualReviewer`) is the strongest guard.

```
Word crop ──► Gemini Vision ──► reading only
                                    │
                                    ▼
                    Python computes similarity vs original / candidate
```

**Gemini never receives** `original_word`, `candidate`, `sentence`, or any context. It
cannot pick a word because it knows which one we want. Its only job is to read what it
sees. One request per case.

A reading collapses to `UNCLEAR` when:
- confidence `< 0.72`
- the reading is not a single Arabic word
- the reading is roughly equidistant from original and candidate

`UNCLEAR` contributes **zero** evidence — not weak evidence.

### 2.8 Applying an accepted correction

Only after `decide()` returns `ACCEPT`. The replacement uses a **verified character span**
found by `_locate_unique_word_span()`, never `str.replace(old, new, 1)`:

1. the sentence must occur **exactly once** on the page
2. the target word must occur **exactly once** inside that sentence
3. matching uses Arabic **token boundaries** — `حسن` cannot match inside `المحسن`

If any condition fails, the original is kept and the case is marked `REVIEW`.

Diacritics-only candidates are not treated as OCR corrections at all; they are deferred to
Stage 4, which has actual image evidence for تشكيل.

---

## The decision engine

**Module:** `ocr_verifier/decision.py` · function `decide()`

All evidence is normalized to `[0, 1]`, then fused. Two derived quantities drive everything:

```
gemini_candidate_evidence = (gemini_supports_candidate * 0.55
                           + similarity(candidate, gemini_reading) * 0.45)
                          * (0.50 + gemini_confidence * 0.50)

candidate_visual_consensus = lighton_support_candidate * 0.50
                           + gemini_candidate_evidence  * 0.50
```

(and the symmetric pair for `original`). When Gemini says `UNCLEAR`, **both** evidence
terms are forced to `0.0`, so an inconclusive reading can never accumulate with context and
validation scores to produce an `ACCEPT`.

Rules are evaluated in order:

| # | Condition | Result |
|---|---|---|
| 1 | LightOn supports original ≥ 0.90 **and** Gemini original evidence ≥ 0.85 **and** original consensus > candidate consensus | `REVIEW`, score capped at `review_threshold + 0.05` |
| 2 | Original consensus ≥ 0.80 **and** leads candidate by ≥ 0.12 | `REVIEW`, same cap |
| 3 | LightOn ≥ 0.90 **and** Gemini ≥ 0.90 **and** context ≥ 0.90 **and** validation ≥ 0.90 | `ACCEPT` — the strongest case, everything agrees |
| 4 | Gemini evidence ≥ 0.92, confidence ≥ 0.90, LightOn doesn't strongly back original, context ≥ 0.92, validation ≥ 0.90 | `ACCEPT` if the fused score clears `ACCEPT_THRESHOLD` |
| 5 | **Hard rule:** Gemini `UNCLEAR` **and** normalized letters differ | `REVIEW` at best, otherwise `REJECT` — score force-capped below `ACCEPT_THRESHOLD` |
| 6 | Candidate visual consensus < 0.60 **and** letters differ | `REVIEW` if above review threshold, else `REJECT` |
| 7 | Fused score ≥ `ACCEPT_THRESHOLD` **but** letters differ **and** (consensus < 0.82 **or** gap < 0.12 **or** `UNCLEAR`) | downgraded to `REVIEW` |
| 8 | Fused score ≥ `ACCEPT_THRESHOLD` | `ACCEPT` |
| 9 | Fused score ≥ `REVIEW_THRESHOLD` | `REVIEW` |
| 10 | otherwise | `REJECT` |

Fallback fused score:

```
final = context * 0.25 + lighton_visual * 0.20 + gemini_candidate_evidence * 0.35 + validation * 0.20
```

The asymmetry is deliberate: rules 1, 2, 5, 6 and 7 all exist to *block* changes. There is
no rule that promotes a change on context strength alone.

---

## Stage 3 — Final context review (opt-in)

**Module:** `ocr_verifier/final_context_reviewer.py` · **Endpoint:** `POST /api/v1/review/final-text`

**Disabled by default** in `run_full_pipeline.py`. Enable with `--enable-final-context-review`.

This stage sees only text — no image. It is therefore deliberately not a free-form
corrector. Its job is small, obvious residual OCR errors after Stage 2.

`clean_ocr_noise()` first removes non-story artifacts (page comments, HTML, notes, page
numbers) without correcting words, rewriting sentences, adding diacritics or inferring
missing text.

Then `_validate_result()` enforces:

- **Speaker labels are protected.** `_validate_speaker_labels()` rejects any change to a
  speaker/person label. If a label really is wrong, that's the visual reviewer's job,
  not a guess from context.
- **Large edits are rejected** and marked for visual review.
- **`UnsafeReviewError` is not retried.** Repeating a semantic guess costs another API call
  and can produce a *different* guess. Only transient failures are retried.
- **`_word_change_stats()`** counts changes by sequence alignment, not index-by-index, so
  one split/merged word doesn't mark the whole rest of the paragraph as changed.

Returns a `FinalReviewResult` with `changed`, `attempts`, `review_required`,
`rejection_reason`, `changed_words`, `changed_ratio`.

---

## Stage 4 — Visual diacritization

**Module:** `ocr_verifier/diacritization/` · **Endpoint:** `POST /api/v1/diacritization/page`

Arabic diacritics (تشكيل) are read **from the image**, never inferred from grammar.

```
corrected_text ──► reference Arabic words
                            │
page image ──► PaddleOCR ──► bounding boxes
                            │
                    DP alignment (not index-to-index!)
                            │
                    per-word crop ──► Gemini Vision
                            │
                    preservation check
                            │
                    rebuild_text() ──► final text
```

### Alignment

`_align_words()` uses **dynamic programming**, not `reference[i] ↔ box[i]`, because
PaddleOCR may miss a word, emit an extra box, or split one word into two. Three operations:
`MATCH`, `SKIP_REFERENCE` (word present in text, no box found), `SKIP_BOX` (spurious box).

Boxes are pre-filtered to Arabic-containing ones and sorted in reading order: top-to-bottom,
then **right-to-left** within a line.

### The 0.72 gate

`MIN_DIACRITICS_ALIGNMENT = 0.72` (`word_diacritics.py:1085`). Below that, the word is
marked `REVIEW` and **no crop is sent to the API** — transferring diacritics from a
neighbouring word is worse than transferring none.

### Preservation

Gemini is instructed it may not change words, letters, spelling, phrasing, may not correct
OCR, and may not add diacritics from grammatical rules. The image is the only source.

`_check_preservation()` verifies this mechanically: both texts are compared with diacritics
stripped. **Any** difference in Arabic letters or word order fails the stage.
`_find_first_difference()` reports where, for debugging.

### Rebuilding

`rebuild_text.py` merges results back into `corrected_text`, which stays the source of
truth for everything except the diacritic marks themselves.
`tokenize_preserving_layout()` keeps spaces, line breaks, punctuation, quotes and numbers
byte-exact. Only words with `status == "OK"` receive visual diacritics; `REVIEW` words keep
whatever they had.

Note: existing OCR diacritics are **not** preserved just because they exist — the presence
of two marks in the OCR output doesn't mean they match the original image.

`visual_diacritics.py` is the whole-page variant of the same approach, with the same
preservation guarantee. Its retry logic is capped at **two** attempts for transient errors
(a previous version forced at least five).

---

## Stage 5 — Dual-voice TTS

**Module:** `ocr_verifier/tts/` · **Endpoint:** `POST /api/v1/tts/generate`

```
final Arabic text ──► TextChunker ──► GeminiTTS ──► chunk_0001.wav, chunk_0002.wav, …
                                                          │
                                                    AudioManager
                                                          │
                                              final_audio.wav + report.json
```

### Chunking

`text_chunker.py` splits on `. ! ؟ ? : ؛`, keeping the punctuation with the preceding
sentence. Default `max_chars` is **700** in `TTSPipeline`, **900** in `TextChunker`'s own
default; minimum enforced is 100. Over-long sentences are split at commas first, then at
word boundaries as a last resort.

`clean_text()` normalizes whitespace **only** — it never strips diacritics, changes letters
or corrects text.

### Voices

| Narrator | Default voice | Env var |
|---|---|---|
| female | `Leda` | `GEMINI_TTS_FEMALE_VOICE` |
| male | `Orus` | `GEMINI_TTS_MALE_VOICE` |

Output is 24 kHz WAV.

### Quality gate

`_duration_is_sane()` compares audio duration against the chunk's Arabic word count. An
abnormally short chunk (silence) or long chunk (repetition) is **regenerated once**. If it
fails again, the audio is not merged and not accepted — the request fails rather than
returning bad audio.

### Merging

`audio_manager.py` merges chunk WAVs in order, first validating that all files share the
same channel count, sample width and sample rate.

### Page-level budget guard

In `run_full_pipeline.py`: if a page's diacritics `REVIEW` rate is **≥ 50%**, TTS is skipped
for that page and the page is marked `DIACRITICS_REVIEW_REQUIRED`. No point narrating text
we don't trust.

---

## Caching

Every expensive call is cached, and every cache key is fingerprinted so stale results can't
leak across configuration changes.

| Cache | Location | Key includes |
|---|---|---|
| OCR page text | `<output>/pages/` | PDF SHA-256, DPI, OCR model, `max_side` |
| Visual (Gemini) results | `<output>/` per page | word index, original, candidate, bbox |
| Context reviewer results | `<output>/` per page | hash of the page text |
| Word diacritics | next to `word_crops/` | reference word, bbox, alignment score |
| TTS chunks | next to the chunk WAV | text, model, voice, narrator |

The diacritics cache stores **only already-validated** visual results, so a rerun of the
same page with the same reference word and bbox costs nothing.

---

## Data models

**`ocr_verifier/models.py`**

`Status = Literal["ACCEPT", "REJECT", "REVIEW"]`

`WordBox` — PaddleOCR output: `text`, `score`, `bbox (x1, y1, x2, y2)`.

`SuspiciousWord` — the full audit trail for one word, serialized to `corrections.jsonl`:

| Group | Fields |
|---|---|
| Basic | `page`, `word`, `sentence`, `score`, `reason` |
| Context candidate | `candidate` |
| PaddleOCR | `bbox`, `bbox_text`, `bbox_match_score` |
| Crops | `word_crop`, `context_crop` |
| LightOn visual | `visual_reading`, `visual_score` |
| Gemini visual | `gemini_visual_reading`, `gemini_visual_confidence`, `gemini_supports_original`, `gemini_supports_candidate`, `gemini_visual_unclear` |
| Context | `context_score` |
| Final | `final_score`, `status` |

Every field that fed the decision is recorded, so any `ACCEPT` can be reconstructed and
challenged after the fact.

**`ocr_verifier/diacritization/word_diacritics.py`**

`WordDiacriticsResult` — `index`, `reference_word`, `visual_word`, `bbox`, `status`
(`OK` / `REVIEW`), `crop_path`, `paddle_text`, `alignment_score`.

**`ocr_verifier/tts/models.py`**

`TTSChunkResult` — `index`, `text`, `audio_path`, `char_count`, `duration_seconds`,
`status`, `error`.
`TTSResult` — `input_name`, `model`, `voice`, `final_audio_path`, chunk counts,
`total_characters`, `total_duration_seconds`, `chunks`.
