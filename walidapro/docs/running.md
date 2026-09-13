# Running the pipeline

There are four entry points. Pick by what you need:

| Entry point | Use when |
|---|---|
| `run.py` | You want OCR + verification only, on the command line |
| `run_full_pipeline.py` | You want the whole book: OCR → review → diacritics → audio |
| `run_api.py` | You want the REST API (stage-by-stage endpoints + job orchestration) |
| `python -m ocr_verifier.pdf_type_detector` | You just want to know which pages need OCR |

- [Installation](#installation)
- [Page selector syntax](#page-selector-syntax)
- [run.py — OCR verification](#runpy--ocr-verification)
- [run_full_pipeline.py — full book](#run_full_pipelinepy--full-book)
- [run_api.py — REST API server](#run_apipy--rest-api-server)
- [Standalone tools](#standalone-tools)
- [Cost control](#cost-control)
- [Troubleshooting](#troubleshooting)

---

## Installation

```bash
cd walidapro

python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# PaddleOCR's runtime is NOT in requirements.txt — install the right build yourself:
pip install paddlepaddle             # CPU
# pip install paddlepaddle-gpu       # CUDA — then set PADDLE_DEVICE=gpu

# For running the test suite:
pip install -r requirements-dev.txt
```

Then configure `.env` — see [configuration.md](configuration.md).

**Declared dependencies:** `fastapi`, `uvicorn[standard]`, `python-multipart`, `pillow`,
`numpy`, `paddleocr`, `python-dotenv`, `requests`, `openai`, `google-genai`, `pypdfium2`,
`pymupdf`, `PyJWT[crypto]`.

Note that PaddleOCR downloads its detection and recognition models on first use, so the
first run is slow and needs network access even if everything else is local.

---

## Page selector syntax

Every entry point that takes `--pages` uses the same 1-based syntax:

| Value | Pages processed |
|---|---|
| `3` | page 3 only |
| `1-16` | pages 1 through 16 |
| `1,3,5` | pages 1, 3 and 5 |
| `1-4,8-12` | pages 1–4 and 8–12 |
| *(omitted)* | all pages — `run.py` and the API only; `run_full_pipeline.py` **requires** `--pages` |

Out-of-range pages are silently dropped; the result is sorted and deduplicated.

---

## `run.py` — OCR verification

```bash
python run.py <input.pdf> [-o OUTPUT_DIR] [--pages SPEC] [--dpi N]
```

| Flag | Default | Meaning |
|---|---|---|
| `input` | *(required)* | Path to the PDF. Must have a `.pdf` extension. |
| `-o`, `--output` | `output` | Output directory. |
| `--pages` | all pages | See [page selector syntax](#page-selector-syntax). |
| `--dpi` | `OCR_DPI` env, else `220` | Render DPI. |

**Example**

```bash
python run.py assets/book.pdf --pages 1-5 --dpi 300 -o output/book_run1
```

### Output

```
output/
├── original_ocr.md          Raw LightOn OCR, pages joined with "---", each prefixed <!-- page N -->
├── corrected_text.md        Same, after accepted corrections
├── corrections.jsonl        One JSON object per suspicious word — the full audit trail
├── report.json              { pages, total_words, suspicious_words, accepted, rejected, manual_review }
├── verification_report.md   Human-readable: accepted / manual-review / rejected lists + before/after
├── pages/                   Cached per-page OCR text (fingerprinted)
└── crops/                   Word crops, context crops and the 5 processing variants
```

`corrections.jsonl` is the interesting one — each line is a serialized `SuspiciousWord`
carrying every score that fed the decision (see
[architecture.md § Data models](architecture.md#data-models)). Any `ACCEPT` can be
reconstructed and challenged after the fact.

A page that throws is caught, logged, and recorded in both text outputs as
`<!-- PAGE FAILED: ... -->` — one bad page does not abort the run.

---

## `run_full_pipeline.py` — full book

```bash
python run_full_pipeline.py <input.pdf> --pages SPEC [-o OUTPUT_DIR] [--dpi N] \
                            [--enable-final-context-review]
```

| Flag | Default | Meaning |
|---|---|---|
| `input_file` | *(required)* | Path to the PDF. |
| `--pages` | **required** | See [page selector syntax](#page-selector-syntax). |
| `-o`, `--output` | `full_pipeline_output` | Book output directory. |
| `--dpi` | `220` | Render DPI. |
| `--enable-final-context-review` | off | Enables the text-only final audit (Stage 3). Off by default: automatic letter changes without image evidence are unsafe and cost extra API calls. |

**Example**

```bash
python run_full_pipeline.py assets/book.pdf --pages 1-12 -o full_pipeline_output/mybook
```

> ⚠️ This entry point does not currently start from a clean checkout — it imports
> `TTSPipeline` at module level and `ocr_verifier/tts/gemini_tts.py` is empty. See
> [development.md § Known gaps](development.md#known-gaps-in-the-current-working-tree).

### Per-page isolation

Every page gets its own directory and is fully independent. A failure on page 7 does not
stop pages 8–12.

```
full_pipeline_output/
├── book_report.json                    Aggregate report (see below)
│
├── page_001/
│   ├── page_status.json                Live stage progress — written after every stage
│   ├── original_page.png               Rendered source page
│   │
│   ├── original_ocr.md                 ┐
│   ├── corrected_text.md               │ Stage 2 output
│   ├── verification_report.md          │ (same layout as run.py)
│   ├── report.json                     ┘
│   ├── pages/                          OCR cache
│   ├── crops/                          Word/context/variant crops
│   │
│   ├── final_review/
│   │   ├── before_final_review.txt     Input to Stage 3
│   │   ├── final_corrected_text.txt    Output (identical to input when Stage 3 is off)
│   │   └── report.json                 changed / attempts / review_required / rejection_reason
│   │
│   ├── diacritization/
│   │   ├── final_diacritized_text.txt  Stage 4 output — the narration text
│   │   ├── review_words.txt            Words that did not get visual diacritics
│   │   ├── report.json                 total_words, review_words, review rate
│   │   └── word_crops/                 Per-word crops + the diacritics cache
│   │
│   ├── tts/
│   │   ├── female/                     Leda
│   │   │   ├── input_text.txt
│   │   │   ├── chunks/chunk_0001.wav, chunk_0002.wav, …
│   │   │   ├── final_audio.wav
│   │   │   └── report.json
│   │   └── male/                       Orus — same layout
│   │
│   ├── dual_tts_report.json            Both narrators side by side
│   ├── page_report.json                Everything about this page
│   └── error.txt                       Only present if the page failed
│
├── page_002/  …
```

### Page statuses

| Status | Meaning |
|---|---|
| `SUCCESS` | All enabled stages completed. |
| `REVIEW_REQUIRED` | Stage 3 rejected its own output as too aggressive. |
| `DIACRITICS_REVIEW_REQUIRED` | ≥ 50% of words failed diacritics alignment ⇒ **TTS was skipped** for this page. |
| `FAILED` | An exception occurred. Details in `error.txt` and `page_report.json`. |

`page_status.json` is written after *every* stage, so you can watch progress live:

```bash
watch -n 2 'cat full_pipeline_output/page_003/page_status.json'
```

### `book_report.json`

```jsonc
{
  "ocr":            { "total_words": …, "suspicious_words": …,
                      "accepted": …, "rejected": …, "manual_review": … },
  "diacritization": { "total_words": …, "review_words": …, "review_rate_percent": … },
  "tts": {
    "female": { "voice": "Leda", "pages_ready": …, "total_duration_seconds": … },
    "male":   { "voice": "Orus", "pages_ready": …, "total_duration_seconds": … }
  },
  "pages":                 [ /* one entry per page */ ],
  "review_required_pages": [ 3, 7 ],
  "failed_pages":          [ 11 ]
}
```

Start every post-run review here: check `failed_pages`, then `review_required_pages`, then
`diacritization.review_rate_percent`.

---

## `run_api.py` — REST API server

```bash
python run_api.py [--host HOST] [--port PORT] [--reload]
```

| Flag | Default |
|---|---|
| `--host` | `0.0.0.0` |
| `--port` | `8080` |
| `--reload` | off — enable for development |

```bash
python run_api.py --reload
```

Then open:

- **http://localhost:8080/docs** — interactive Swagger UI
- **http://localhost:8080/redoc** — ReDoc
- **http://localhost:8080/openapi.json** — raw OpenAPI schema

Equivalent direct invocation:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

> ⚠️ The server does not currently start from a clean checkout — see
> [development.md § Known gaps](development.md#known-gaps-in-the-current-working-tree).

CORS is wide open (`allow_origins=["*"]`). Tighten `api/main.py` before exposing this
beyond a trusted network.

Endpoint reference: [api-reference.md](api-reference.md).

---

## Standalone tools

### PDF type detection

```bash
python -m ocr_verifier.pdf_type_detector book.pdf
```

Prints a per-page table and an overall verdict. Fully local, free, no API keys. Run this
first on any new book to see how much OCR you're actually paying for.

```
Page 001 | OCR |  OCR_REQUIRED | Words:    0 | Chars:     0 | Images:  1
          Reason: No text layer found
…
PDF TYPE: OCR_REQUIRED
```

### Diacritization and TTS test harnesses

```bash
python -m ocr_verifier.diacritization.run_word_diacritics_test
python -m ocr_verifier.tts.run_tts_test
python -m ocr_verifier.tts.test_voices
```

Development scratch scripts for exercising a single stage. They call paid APIs.

---

## Cost control

Every stage that costs money has a brake. In rough order of impact:

1. **Inspect before you OCR.** `pdf_type_detector` is free; pages with a good text layer
   don't need LightOn at all.
2. **Caching is on by default and survives reruns.** OCR text, context-reviewer verdicts,
   Gemini visual readings, word diacritics and TTS chunks are all cached with fingerprints.
   Rerunning the same page with the same settings costs ~nothing. Changing DPI, model or
   `LIGHTON_MAX_SIDE` correctly invalidates the OCR cache.
3. **Variant voting stops early.** LightOn crop re-reads stop at 2 requests when two
   variants agree, instead of always spending 5.
4. **Stage 3 is off by default.** It's an extra model call per page, on a Pro-tier model.
5. **Low-alignment words never reach the API.** Below 0.72 alignment, no crop is sent.
6. **TTS is skipped on untrusted pages.** ≥ 50% diacritics review ⇒ no audio generated.
7. **Start small.** `--pages 1-2` on a new book before committing to `--pages 1-120`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError` on `ContextReviewer()` construction | `REVIEWER_MODEL` unset | Set it in `.env` — it has no default by design. |
| `ModuleNotFoundError: paddle` | `paddlepaddle` not installed | `pip install paddlepaddle` (or `-gpu`). Not in `requirements.txt`. |
| PaddleOCR crashes or hangs on Windows/CPU | oneDNN / PIR API | Already mitigated by the `FLAGS_*` env vars set at the top of `bbox_detector.py` — don't remove them. |
| First run very slow | PaddleOCR downloading models | One-off. Needs network access. |
| `LightOn OCR failed: …` after 3 attempts | Endpoint down, wrong URL, or rate limited | Check `LIGHTON_BASE_URL`; the client already retries 429/5xx with backoff. |
| Everything comes back `REVIEW`, nothing `ACCEPT` | Working as designed when visual evidence is weak | Check `corrections.jsonl` for `gemini_visual_unclear` and the consensus scores. Raise DPI before touching thresholds. |
| Diacritization returns mostly `REVIEW` | PaddleOCR alignment below 0.72 | Raise `--dpi`; diacritics are small and need resolution. |
| TTS fails with "quality gate rejected" | Duration didn't match word count twice | Usually a bad chunk. Check `tts/*/report.json` for the failing chunk index. |
| `ImportError: cannot import name 'GeminiTTS'` | `ocr_verifier/tts/gemini_tts.py` is empty in the working tree. `run_full_pipeline.py` hits this **at import time**, before doing any work | See [development.md § Known gaps](development.md#known-gaps-in-the-current-working-tree). |
| API server won't start | Empty router modules | Same — see the known-gaps section. |
