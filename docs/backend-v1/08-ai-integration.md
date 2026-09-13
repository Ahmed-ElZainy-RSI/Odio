# AI Integration Architecture

How 13,772 lines of existing AI logic plug into the service without either side learning about the
other.

## 1. The ports

These are **specifications**, not implementations. Signature, contract, and error behaviour only. They
live in `ai_engine/ports/` and may import nothing but `ai_engine/domain/`.

```python
# ai_engine/ports/ocr.py
from typing import Protocol, Sequence
from ai_engine.domain.tokens import Token, PageProvenance

class OcrEngine(Protocol):
    """Turn one rendered page (or one PDF page) into an ordered token sequence.

    Contract:
      - Tokens are returned in Arabic reading order (right to left within a line,
        top to bottom across lines).
      - Every token carries a stable id of the form f"p{page:03d}w{index:03d}".
        Ids are a pure function of (page, index) and MUST be reproducible.
      - `box` is present only if the engine supplies geometry. LightOn parse-v3
        returns markdown with no geometry at all (box_level="none").
      - An empty page (illustration only) returns an EMPTY sequence, not an error.

    Raises:
      OcrError            - the provider failed or returned unusable output. Retryable
                            flag is set on the exception.
      OcrUnavailableError - the provider is down or over quota. Always retryable.
    Never raises SystemExit. Never returns partial results silently.
    """
    name: str
    model_id: str

    def extract_page(self, page_image: bytes | None, pdf_path: str | None,
                     page_number: int) -> tuple[Sequence[Token], PageProvenance]: ...
```

```python
# ai_engine/ports/diacritizer.py
class Diacritizer(Protocol):
    """Add Arabic diacritics (tashkeel) to a token sequence.

    HARD INVARIANT, inherited from the existing implementations and NOT negotiable:
    the consonantal skeleton must not change. For every token,
    strip_diacritics(out.txt) == strip_diacritics(in.txt).
    An implementation that cannot honour this for a token MUST leave that token
    unshaped and mark it for review, never guess.

    Returns the tokens plus a DiacritizationReport carrying `review_ratio`, which
    the caller uses as a quality gate.
    """
    name: str
    model_id: str

    def diacritize(self, tokens: Sequence[Token], page_image: bytes | None,
                   page_number: int) -> tuple[Sequence[Token], DiacritizationReport]: ...
```

```python
# ai_engine/ports/tts.py
class TtsEngine(Protocol):
    """Synthesize one page of text into a single audio buffer.

    Contract:
      - Returns raw bytes plus an AudioFormat describing them exactly
        (codec, sample_rate_hz, channels, bit_depth, duration_ms).
      - duration_ms MUST be measured from the returned audio, never estimated
        from character count.
      - `native_word_timestamps` is non-None only for engines that provide them
        (Hume does; Gemini has no timestamp API at all).
    """
    name: str
    model_id: str

    def synthesize(self, text: str, voice: str,
                   narrator: str) -> tuple[bytes, AudioFormat, list[WordStamp] | None]: ...
```

```python
# ai_engine/ports/aligner.py
class Aligner(Protocol):
    """Bind word-level timings onto stable token ids.

    Contract:
      - Every returned token either has t=[start_ms,end_ms] with t_src set,
        or has no `t` at all. There is no third state.
      - Timings are monotonic and non-overlapping across the page.
      - All timings lie within [0, duration_ms].
      - A BindReport is always returned, even when zero anchors were found —
        zero anchors is a DEGRADED result, not an error.
    """
    method: str  # e.g. "forced-whisper-small" | "native-hume"

    def align(self, audio: bytes, fmt: AudioFormat, tokens: Sequence[Token],
              native_stamps: list[WordStamp] | None) -> tuple[Sequence[Token], BindReport]: ...
```

```python
# ai_engine/ports/page_verifier.py
class PageVerifier(Protocol):
    """Verified tier only. Audit and correct a page's OCR text using visual evidence.

    Contract:
      - MAY return the input unchanged with review_required=True. That is a
        legitimate, expected outcome, not a failure.
      - MUST distinguish "the model is uncertain" (review_required) from
        "the provider is broken" (raise). The existing implementation conflates
        these; see walidapro/.../word_diacritics.py:1296.
    """
    def verify_page(self, text: str, page_image: bytes,
                    page_number: int) -> VerificationResult: ...
```

Plus four infrastructure ports with no AI content: `SourceFetcher`, `PageRenderer`,
`ArtifactUploader`, `JobCallbacks`. See [06-service-boundaries.md](06-service-boundaries.md).

## 2. Adapter inventory

| Port | V1 adapter | Wraps | What the adapter must add |
|---|---|---|---|
| `PageRenderer` | `PyMuPdfRenderer` | `src/pipeline/ocr_gemini.py:56 render_page`; alternative at `walidapro/ocr_verifier/pdf_render.py:41` | A **streaming** page-at-a-time API. `pdf_render.py:41-107` currently returns a full `list[(page_no, PIL.Image)]` — ~14 MB of RGB per page at 220 DPI, so ~1.4 GB for a 100-page book before any work starts. Also expose `page_count(path) -> int`, which `pdf_render.py:58` computes and then discards. |
| `OcrEngine` (text layer) | `TextLayerOcr` | `src/pipeline/extract.py:85 extract_page` + `:39 clean_word` | Nothing behavioural — it is pure and free. Just injected config and structured errors. |
| `OcrEngine` (Gemini) | `GeminiVisionOcr` | `src/pipeline/ocr_gemini.py:65 ocr_page`, `:87 lines_to_tokens` | Explicit timeout; status-aware retry (the current `retries=4` loop does not distinguish 429 from 400); model id from config, not the `MODEL` constant at `:28`. |
| `OcrEngine` (LightOn) | `LightOnOcr` | `src/pipeline/ocr_lighton.py:65 parse_page`, `:75 clean_markdown` | Retry (there is currently **none** — `:66` has no retry loop at all); timeout; base URL from config rather than the hardcoded `https://api.lighton.ai/api/v3/parse` at `:41`. |
| `Diacritizer` (fast) | `LocalBiLstmDiacritizer` | `src/pipeline/tashkel.py:35 diacritize_tokens` | **Kill the module-global `_model` singleton** at `:26-33`; accept an injected, warmed instance. Preserve the positional id matching and the abandon-page-on-count-mismatch behaviour at `:50`. |
| `Diacritizer` (verified) | `VisualWordDiacritizer` | `walidapro/ocr_verifier/diacritization/word_diacritics.py:1509 extract_page` | Parallelize the per-word loop at `:1611-1660`; re-key the cache on content rather than output-directory location (`:1020-1049`); replace the lock-free whole-file rewrite (`:1143`→`:1405`); return bytes rather than writing crops and traces to a debug directory; **distinguish outage from ambiguity** instead of blanket-swallowing at `:1296`. Keep `:1085`'s 0.72 alignment gate, `:1275`'s letters-unchanged check and `rebuild_text.py:173` with both preservation assertions **verbatim**. |
| `TtsEngine` (Gemini) | `GeminiTts` | `src/pipeline/tts_gemini.py:21 synthesize`; optionally `walidapro/ocr_verifier/tts/gemini_tts.py` for the Arabic narration prompts and chunking | Timeout (there is none); retry; return bytes rather than writing a file. Keep `walidapro/.../tts/pipeline.py:195 _duration_is_sane` — it encodes a real observed failure (a degenerate ~224 s output for 50 words, called out at `:493-495`). |
| `TtsEngine` (Hume) | `HumeTts` | `src/pipeline/tts_hume.py:15` | **Do not call `src/pipeline/run_speak_hume.py:69 run()`** — it wraps itself in `asyncio.run()` at `:77` and raises `RuntimeError` from inside any running event loop. Call the async client directly, or run it on a dedicated loop. |
| `Aligner` | `WhisperForcedAligner` | `src/pipeline/align_whisper.py:26 align_words` + **`src/pipeline/speak.py:99 bind` verbatim** | Kill the module-global `_model` singleton at `align_whisper.py:16-23`; make `device`/`compute_type` configurable (they are hardcoded at `:22`). `bind` needs **no changes at all** — it is pure, engine-agnostic, dependency-free, and already exercised by a second caller with a different token shape (`tools/build_review.py:105`). |
| `PageVerifier` | `WalidaproVerifier` | `walidapro/ocr_verifier/pipeline.py:863 process_page`, `final_context_reviewer.py:749 review` | Discard `run_pdf` (`:1796`) and `save_outputs` (`:1873`) entirely — they are document-batch, and `save_outputs` runs once after the whole loop so a crash before the last page yields zero output. Fix the double-retry amplification (`final_context_reviewer.py:807`/`:251` app-3× against the SDK's un-overridden `max_retries=2` = up to 9 billed calls). Add a timeout (`:825` passes none, so the SDK's 600 s read default applies). Replace ~20 `print()` calls with the structured logger. |

## 3. Component decision matrix

The most important table in this document set. **Every `API?` cell is `No`.** The only HTTP surface in
V1 is `POST /api/jobs` plus probes.

| Existing Component | Current Role | API? | Service? | Internal Module? | Worker? | Reason |
|---|---|:---:|:---:|:---:|:---:|---|
| `src/pipeline/extract.py` | text-layer extraction + 4 cleaning rules | No | No | **Yes** | runs in | Pure, free, unit-tested with controls. Nothing to expose. |
| `src/pipeline/pages.py` | 1-based page-spec parser | No | No | **Yes** | — | 30 lines of pure parsing. Reused for the 1-based convention. |
| `src/pipeline/speak.py:bind` | timestamp → token binding | No | No | **Yes** | runs in | Pure, µs, engine-agnostic. The single most valuable reusable function in the repo. |
| `src/pipeline/align_whisper.py` | forced alignment | No | No | **Yes** | **Yes** | 464 MB resident, CPU-bound at RTF 0.20 (measured). A worker concern, never an endpoint. |
| `src/pipeline/tashkel.py` | local diacritizer | No | No | **Yes** | **Yes** | In-process model weights. Same reasoning. |
| `src/pipeline/ocr_gemini.py` | Gemini vision OCR | No | No | **Yes** | **Yes** | A provider client. Exposing it would just proxy Gemini. |
| `src/pipeline/ocr_lighton.py` | LightOn parse client | No | No | **Yes** | **Yes** | Same. |
| `src/pipeline/tts_gemini.py`, `tts_hume.py` | TTS clients | No | No | **Yes** | **Yes** | Same. |
| `src/pipeline/run_*.py` (6 drivers) | book-level CLI loops | No | No | No | **replaced** | `break`/`return` on error violates INVARIANT-1; no per-page hook exists. Superseded by the Page Task Executor. |
| `src/pipeline/main.py` | pipeline CLI | No | No | No | **replaced** | Its job is now `RunExtractionJob`. Keep as a dev CLI. |
| `src/pipeline/build_reader.py` | `reader.html` generator | No | No | dev CLI | No | Human artifact. No `artifactType` slot exists for HTML (`Q-11`). |
| `tools/ensemble_review.py` | two-engine token alignment | No | No | **Yes** | — | `normalize_for_compare:34` and `align_page:62` are pure, tested, and carry real Arabic domain knowledge. Reusable for a future consensus tier. |
| `tools/apply_corrections.py`, `apply_context_suggestions.py` | correction merge semantics | No | No | **Yes** (logic) | — | The `ocr_txt` preservation, `fixes[]` append and letter-only staleness check are the correct correction model. Keep the logic, drop the CLI. |
| `tools/replace_page.py` | wholesale page swap | No | No | **Yes** (logic) | — | Same. |
| `tools/vlm_arbiter.py` | vision arbitration of flagged pages | No | No | future tier | No | Automatable in principle, but it belongs to the two-engine consensus flow which V1 does not run. |
| `tools/context_detector.py` | correlated-error detection | No | No | **No** | No | **Human-gated by design**: `:148-160` seeds `context_decisions.json` with every entry defaulting to `"reject"`. Cannot sit inside an automated job. |
| `tools/build_context_review.py` | HTML triage page | No | No | No | No | For a human to click. |
| `tools/build_review.py` | A/B review page + audio | No | No | No | No | For a human to listen to. |
| `tools/run_full_pipeline.py` | 8-step chain | No | No | No | No | `:99-115` prints *"STOPPING HERE ON PURPOSE -- one manual step left"*. |
| `tools/export_session.py` | Claude transcript → Markdown | No | No | No | No | Unrelated developer tooling. |
| `walidapro/ocr_verifier/decision.py` | score fusion | No | No | **Yes** | — | Pure function with an explicit signature. Belongs in a service unchanged. |
| `walidapro/ocr_verifier/models.py` | `SuspiciousWord`, `WordBox` | No | No | **Yes** | — | Serviceable DTOs; `asdict()` already gives a flat JSON record. |
| `walidapro/ocr_verifier/utils.py` | Arabic normalize, atomic write | No | No | **Yes** | — | `atomic_write_text:48` is the tmp-file + `os.replace` pattern the rest of the repo lacks. Promote it. |
| `walidapro/ocr_verifier/reviewer.py` | suspicious-word LLM | No | No | **Yes** | **Yes** | Verified tier. A provider client with domain prompts. |
| `walidapro/ocr_verifier/visual_reviewer.py` | blind-crop vision read | No | No | **Yes** | **Yes** | Same. The blind-crop protocol is the asset. |
| `walidapro/ocr_verifier/final_context_reviewer.py` | final text audit | No | No | **Yes** | **Yes** | Stateless, pure, no disk I/O. Genuinely service-ready in substance. |
| `walidapro/ocr_verifier/pipeline.py` | OCR verification orchestration | No | No | **Yes** (`process_page` only) | **Yes** | Keep `process_page:863`; discard `run_pdf:1796` and `save_outputs:1873`. |
| `walidapro/ocr_verifier/diacritization/*` | per-word visual diacritics | No | No | **Yes** | **Yes** | Verified tier. Must be parallelized before use. |
| `walidapro/ocr_verifier/tts/*` | chunked TTS + WAV merge | No | No | **Yes** | **Yes** | Keep `save_wav`, `merge_wav_files`, `TextChunker.chunk`, the narration prompts, `_duration_is_sane`. |
| `walidapro/ocr_verifier/bbox_detector.py` | PaddleOCR localization | No | No | **Yes** | **Yes** | In-process weights; must be a shared instance. Note it is loaded **twice** today — `pipeline.py:44` and `word_diacritics.py:93`. |
| `walidapro/ocr_verifier/cropper.py`, `pdf_render.py` | crops, rendering | No | No | **Yes** | runs in | Pure. Need disk-free and streaming variants respectively. |
| `walidapro/ocr_verifier/pdf_type_detector.py` | born-digital vs scanned | No | No | No | No | **Verified dead** — nothing imports it. |
| `walidapro/run_full_pipeline.py`, `run.py` | book/page CLI | No | No | No | **replaced** | CLI orchestration incl. the per-page subprocess fork at `:503-529`. |
| `src/tests/*` | 56 hermetic tests | No | No | **keep as-is** | — | Regression net. |

**The principle.** An internal ML function is never automatically an endpoint. Exposing `OcrEngine` over
HTTP would create a second protocol to version, a second auth surface, and a network hop between stages
that share a scratch directory — while adding zero capability, because nothing calls it independently.
The API exists to accept *jobs*, which are business units of work. See
[ADR-001](19-adrs.md) and [ADR-003](19-adrs.md).

## 4. Operational profile per AI component

| Component | Model / provider | Calls per page | Retry (target) | Timeout (target) | Failure mode | Cost driver | Page-fatal? |
|---|---|---|---|---|---|---|---|
| Text-layer extraction | none (PyMuPDF) | 0 | n/a | n/a | malformed PDF | none | Yes → `SOURCE_INVALID` |
| Gemini OCR | `gemini-3.6-flash` | 1 | 3, status-aware | 60 s | 429 quota wall — **real**: `README.md:491` records a 10 req/day free-tier cap | per page | Yes |
| LightOn parse | `lighton/parse-v3` | 1 | 3 (currently **0**) | 60 s | 5xx, markdown-only output | per page | Yes |
| Local diacritizer | `arabic-diacritizer` BiLSTM | 0 | n/a | n/a | token-count mismatch → page abandoned (`tashkel.py:50`) | CPU only | No — degrade to unshaped |
| Visual diacritics (verified) | Gemini vision | **1 per Arabic word** | 2 per word | 120 s | one bad word ≠ one bad page | **per word** — median 35, p90 69, max 137 (measured over 186 pages) | No — degrade to `REVIEW` |
| ContextReviewer (verified) | configurable, OpenAI-compat | 1 | 3 | 90 s | JSON parse failure | per page | No — no corrections found |
| Visual reviewer (verified) | Gemini vision | 1 per suspicious word | 2 | 60 s | `unclear` → no change | per suspicious word | No |
| FinalContextReviewer (verified) | `gemini-3.1-pro-preview` | 1 | **1** (currently 3 × SDK 2 = 9) | 120 s (currently 600 s SDK default) | `UnsafeReviewError` → keep original | per page | No |
| Gemini TTS | `gemini-3.1-flash-tts-preview` | 1 per chunk (typically 1) | 2 | 180 s | degenerate long audio → caught by `_duration_is_sane` | per page | Yes |
| Hume TTS | Hume v2 | 1 | 2 | 180 s | quota (exhausted mid-book historically) | per page | Yes |
| Forced alignment | `faster-whisper small` int8 | 0 (local) | n/a | wall-clock deadline | zero anchors → all interpolated | **CPU**: 5.42 s per 27.5 s audio, RTF 0.20 (measured) | No — degrade |
| `speak.bind` | none | 0 | n/a | n/a | cannot fail | none | No |

## 5. The two quality tiers

`qualityTier` is a per-job field (**PROPOSAL**, [07-api-specification.md](07-api-specification.md) §1.1).

### Tier `fast` — default

Pipeline S. Per page:

| Stage | Paid calls |
|---|---|
| Text layer, if present (25 of 36 sample books — `README.md:444-448`) | **0** |
| …or Gemini/LightOn OCR | **1** |
| Local diacritization | **0** |
| TTS (Job B) | **1** |
| Forced alignment | **0** |
| **Job A total** | **0–1** |
| **Job B total** | **1** |

### Tier `verified` — opt-in, not enabled in V1

Pipeline W. Per page, with `W` suspicious words and `D` alignable Arabic words:

| Stage | Paid calls | Source |
|---|---|---|
| LightOn full-page OCR | 1 | `walidapro/ocr_verifier/pipeline.py:890` |
| ContextReviewer suspicious-word scan | 1 | `reviewer.py:531` |
| Per suspicious word: LightOn crop variants | 2–5 × W | `lighton_ocr.py:271`, capped at 5, early-stop on 2 agreeing votes |
| Per suspicious word: Gemini blind crop read | 1 × W | `visual_reviewer.py:758` |
| Per suspicious word: 2 reviewer scores | 2 × W | `reviewer.py:634`, `:735` |
| FinalContextReviewer | 1 (up to 9 today) | `final_context_reviewer.py:825` |
| **Per-word visual diacritics** | **1 × D** | `word_diacritics.py:1246` |
| TTS, dual narrator | 2 | `run_full_pipeline.py:1057-1130` |

With the measured Arabic-words-per-page distribution over 186 real pages (**median 35, mean 38.1, p90
69, max 137**) and taking `W ≈ 5`:

```text
median page:  1 + 1 + (2..5)×5 + 5 + 10 + 1 + 35 + 2   =  ~65 to ~80 paid calls
p90 page:     1 + 1 + (2..5)×8 + 8 + 16 + 1 + 69 + 2   = ~114 to ~140 paid calls
worst seen:                              ... + 137 ... =  ~200+ paid calls
```

So **fast is 1–2 calls per page and verified is 65–200**, a 40–100× cost multiplier. That is the whole
argument of [ADR-009](19-adrs.md).

### Promotion criteria — what would make `verified` the default

All four must hold:

1. `Q-07` answered as "V1 acceptance is measured on AI accuracy", not protocol conformance.
2. The per-word diacritics loop is parallelized. Today it is one blocking call per word in a plain `for`
   loop (`word_diacritics.py:1611-1660`); at p90 that is 69 sequential round trips per page.
3. A measured cost-per-book figure exists and is approved. Today there is **no cost instrumentation at
   all**, and `README.md:190`'s "$38 / 36 books" is a projection for 3-engine consensus OCR, not an
   invoice, and excludes TTS and the reasoning calls.
4. `walidapro/` has a test suite. It currently has **zero tests across 16,155 LOC** — 83% of production
   Python — and is not even collectible by pytest.

Until then the verified tier is reachable per job for evaluation, and off by default.

## 6. Model versioning and rollback

**Current state: model ids are hardcoded constants.**

| Constant | Location |
|---|---|
| `MODEL = "gemini-3.6-flash"` | `src/pipeline/ocr_gemini.py:28` |
| `MODEL = "gemini-3.1-flash-tts-preview"`, `VOICE = "Kore"` | `src/pipeline/tts_gemini.py:16-17` |
| `VOICE = "Layla, Arabic Philosopher"` | `src/pipeline/tts_hume.py:12` |
| `MODEL = "deepseek/deepseek-v4-pro-0813"` | `tools/context_detector.py:46` |
| `model = "gemini-3.1-pro-preview"` | `walidapro/ocr_verifier/final_context_reviewer.py:246` |
| `ENGINE = "lighton/parse-v3"` | `src/pipeline/ocr_lighton.py:42` |
| `WhisperModel("small", device="cpu", compute_type="int8")` | `src/pipeline/align_whisper.py:19-23` |
| `lighton_model`, `reviewer_model` | `walidapro/ocr_verifier/config.py:14,18` — already env-driven |

**Target.** Every model id, voice and local model size becomes a config key
([15-configuration.md](15-configuration.md)). Rollback is then a config change plus a worker restart —
no code deploy, and no schema change.

**Provenance.** Build on what already exists rather than inventing a scheme. The page records already
carry `ocr_engine`, `tts`, `voice`, `align` and `box_level`
(`work/فلفل المغرور-lighton/p003.json` shows `"tts": "gemini:gemini-3.1-flash-tts-preview"`,
`"align": "forced-whisper-small"`). Extend that convention:

- Every artifact that has a schema (the timing JSON) carries a `provenance` block with the model ids
  and the alignment method.
- Every inference log line carries `model` and `model_version`
  ([13-observability.md](13-observability.md)).
- `ai_engine_inference_duration_seconds{provider,model,stage}` is labelled by model, so a regression
  after a model change is visible in the same dashboard.

The `Image` and `Audio` artifacts are raw bytes with nowhere to put provenance; their provenance lives
in our logs and in the timing JSON only. That asymmetry is worth knowing before someone asks "which TTS
model produced this file".

## 7. Degradation policy

| Situation | Behaviour |
|---|---|
| Provider returns `429` | Retry with backoff, honour `Retry-After`. If the global provider budget is exhausted, **do not** burn attempts — park the page task and let it re-run later. `README.md:491` documents a real free-tier wall of 10 requests/day per model, so this is not hypothetical. |
| Provider returns `5xx` | Retry per the shared policy. Terminal after the cap → page `/failed` with `AI_INFERENCE_ERROR`. |
| Provider returns garbage | Bounded by the existing validators — `_duration_is_sane` for TTS, the letters-unchanged check for diacritics, `_validate_result` for the final reviewer. A validator rejection is a *degradation*, not a failure: keep the original, mark for review, continue. |
| Provider deprecates a model | Config change + restart. This is the main reason model ids must not be constants. |
| Alignment finds zero anchors | Emit the timing JSON with all tokens interpolated, or with no `t` at all if there is no duration to interpolate against. Page still `completed`. The `anchor_ratio` metric carries the quality signal. |
| Local model weights missing | Worker fails `/ready` and does not claim work. Better than claiming and failing every page. |
| **Quality alarm** | `ai_engine_alignment_anchor_ratio` — corpus baseline **0.78 measured** over 95 pages. Per-book it ranges from 51.0% to 93.7% (`README.md:473,491`), so alert on a sustained *fleet* drop, not a single page. |

## 8. Safety invariants that must not be weakened

These are the product's correctness guarantees. They were reached deliberately — the
`walidapro/FUNCTIONAL_AUDIT_REPORT.txt` documents several of them as audited fixes — and any refactor
that loses one is a regression regardless of what the tests say.

| Invariant | Enforced at | Why it matters |
|---|---|---|
| **Diacritization can never change letters.** A model result is accepted only if `remove_diacritics(result) == reference_word`. | `walidapro/.../word_diacritics.py:1275` | Without it, a diacritizer hallucination silently rewrites the book's words. |
| **Layout and word sequence are preserved, asserted.** Two hard `RuntimeError` checks after rebuilding. | `walidapro/.../rebuild_text.py:494`, `:536` | Catches the failure above even if an upstream check is bypassed. |
| **The vision model never sees the candidate answer.** Only the base64 crop is sent — no original word, no candidate, no sentence. | `walidapro/.../visual_reviewer.py:133-150`, `:290-302` | Prevents the model from agreeing with a suggestion it was shown. This is the difference between evidence and confirmation bias. |
| **An unclear visual read can never authorize a letter change.** | `walidapro/ocr_verifier/decision.py:375-409` | Precision bias: a wrong "correction" is worse than an uncorrected OCR error. |
| **Corrections are applied by verified character span, not `str.replace`.** A repeated word is corrected only when its sentence is unique in the page and the token is unique within that sentence; ambiguous occurrences become `REVIEW`. | `walidapro/ocr_verifier/pipeline.py:367 _locate_unique_word_span`; documented as audited fixes 3–6 in `walidapro/FUNCTIONAL_AUDIT_REPORT.txt` | `str.replace(old, new, 1)` corrupts the wrong occurrence. Arabic substring matches are also explicitly blocked so حسن does not match inside المحسن. |
| **The final reviewer refuses rather than guesses.** Arabic-letter ratio within `[0.92, 1.08]`, word-count delta ≤8%, changed-word ratio ≤18%, speaker labels byte-identical — else `UnsafeReviewError` and return the original text with `review_required=True`, deliberately **not** retried. | `walidapro/.../final_context_reviewer.py:601`, `:518`, `:485`, `:933` | Turns an unbounded LLM rewrite into a bounded, auditable edit. This maps almost one-to-one onto a `/failed {reason}` callback. |
| **Diacritics-only OCR candidates are not treated as OCR corrections**; they are deferred to the visual diacritics stage. | audited fix 7, `walidapro/FUNCTIONAL_AUDIT_REPORT.txt` | Keeps the two concerns from fighting each other. |
| **Token ids are the anchor and never change.** `src/pipeline/tashkel.py:19-22` calls this out as load-bearing: boxes, timings, `ensemble_review` rows and `context_decisions` keys all reference them. | throughout | A renumbered page invalidates every downstream reference. |
