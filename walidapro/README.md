# Arabic Book Pipeline — OCR Verification, Diacritization & TTS

An Arabic-language document processing engine that turns a scanned children's/educational
book PDF into **verified text**, **visually-sourced diacritics (تشكيل)**, and
**narrated audio** — while refusing to guess.

The guiding rule of the whole codebase: **uncertain ⇒ keep the original and flag for
review.** No stage is allowed to change an Arabic letter unless independent *visual*
evidence supports the change.

---

## Table of contents

| Document | What's in it |
|---|---|
| **README.md** (this file) | Overview, quickstart, project layout |
| [docs/architecture.md](docs/architecture.md) | The five pipeline stages, decision engine, safety guards, caching |
| [docs/configuration.md](docs/configuration.md) | Every environment variable, defaults, tuning thresholds |
| [docs/running.md](docs/running.md) | All four ways to run it + output artifact reference |
| [docs/api-reference.md](docs/api-reference.md) | Every REST endpoint, request/response shapes, error codes |
| [docs/backend-contract.md](docs/backend-contract.md) | The Backend ⇄ AI Engine job contract (Keycloak, S3, callbacks) |
| [docs/development.md](docs/development.md) | Tests, code conventions, **known gaps in the current working tree** |
| [docs/procurement-request.html](docs/procurement-request.html) | **For IT/Finance** — supplier accounts to open, plans to pick, and the budget for a 1,000-book run |

> ⚠️ **Read [docs/development.md § Known gaps](docs/development.md#known-gaps-in-the-current-working-tree) before running anything.**
> Several modules are currently empty (0 bytes) in the working tree, which prevents the
> API server from starting and the TTS pipeline from importing. The gaps and the exact
> missing surface are documented there.

---

## What it does

```
  Scanned Arabic PDF
        │
        ├─ 1. PDF TYPE DETECTION ──── does this page even need OCR?
        │                             (PyMuPDF text-layer analysis)
        │
        ├─ 2. OCR + VERIFICATION ──── LightOn OCR reads the page
        │                             Gemini (text) flags suspicious words
        │                             PaddleOCR locates the word on the page
        │                             LightOn re-reads 5 image variants of the crop
        │                             Gemini Vision reads the crop BLIND
        │                             decision.py fuses the evidence → ACCEPT/REVIEW/REJECT
        │
        ├─ 3. FINAL CONTEXT REVIEW ── optional, OFF by default (text-only, no image)
        │
        ├─ 4. VISUAL DIACRITIZATION ─ per-word crops → Gemini Vision reads the تشكيل
        │                             letters/punctuation/order provably unchanged
        │
        └─ 5. DUAL-VOICE TTS ──────── Gemini TTS, female (Leda) + male (Orus)
                                      duration sanity-check catches silence/repetition
```

Each stage is independently runnable, cached, and auditable.

---

## Quickstart

### Requirements

- **Python 3.10+** (developed on 3.13)
- A **LightOn OCR** endpoint (self-hosted or cloud)
- A **Gemini API key** (used for the context reviewer, visual reviewer, diacritics and TTS)

### Install

```bash
cd walidapro

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# PaddleOCR needs its runtime, which requirements.txt does NOT pin.
# Install the build that matches your machine:
pip install paddlepaddle           # CPU
# pip install paddlepaddle-gpu     # CUDA
```

### Configure

```bash
cp .env.example .env
$EDITOR .env
```

At minimum you need `LIGHTON_BASE_URL`, `REVIEWER_API_KEY` and `REVIEWER_MODEL`.
`ContextReviewer()` raises immediately if `REVIEWER_MODEL` is unset — it has no default
on purpose. See [docs/configuration.md](docs/configuration.md) for all variables.

### Run

```bash
# OCR verification only, pages 1–5
python run.py book.pdf --pages 1-5 -o output/

# Full book: OCR → review → diacritics → dual-voice audio
python run_full_pipeline.py book.pdf --pages 1-12 -o full_pipeline_output/

# REST API (http://localhost:8080/docs for interactive Swagger UI)
python run_api.py --reload
```

More detail, including the page-selector syntax and every output file produced:
[docs/running.md](docs/running.md).

---

## Project layout

```
walidapro/
├── run.py                     CLI: OCR verification only
├── run_full_pipeline.py       CLI: full book orchestrator (all 5 stages, per-page isolation)
├── run_api.py                 CLI: uvicorn launcher for the REST API
│
├── ocr_verifier/              ── THE ENGINE ──
│   ├── config.py              Settings dataclass, all env vars + thresholds
│   ├── models.py              WordBox, SuspiciousWord, Status literals
│   ├── utils.py               Arabic normalization, tokenizing, atomic writes
│   │
│   ├── pdf_type_detector.py   Stage 1 — TEXT_LAYER vs OCR_REQUIRED per page
│   ├── pdf_render.py          PDF page → PIL image (pypdfium2), page-selector parsing
│   │
│   ├── pipeline.py            Stage 2 orchestrator (2237 lines) — the core loop
│   ├── lighton_ocr.py         LightOn Parse API client + adaptive variant voting
│   ├── reviewer.py            ContextReviewer — text-only suspicious-word detection
│   ├── bbox_detector.py       PaddleOCR — localization only, never the OCR source
│   ├── cropper.py             Word/context crops + 5 image-processing variants
│   ├── visual_reviewer.py     GeminiVisualReviewer — BLIND crop reading
│   ├── decision.py            Evidence fusion → ACCEPT / REVIEW / REJECT
│   ├── final_context_reviewer.py  Stage 3 — conservative text-only audit (opt-in)
│   │
│   ├── diacritization/        ── STAGE 4 ──
│   │   ├── word_diacritics.py     Per-word crop → Gemini Vision → visual تشكيل
│   │   ├── visual_diacritics.py   Whole-page variant of the same idea
│   │   └── rebuild_text.py        Merge diacritics back, preserving layout exactly
│   │
│   └── tts/                   ── STAGE 5 ──
│       ├── pipeline.py            Chunk → synthesize → merge → report
│       ├── gemini_tts.py          Gemini TTS engine (Leda / Orus)
│       ├── text_chunker.py        Sentence-aware Arabic chunking
│       ├── audio_manager.py       WAV merging with format validation
│       └── models.py              TTSChunkResult, TTSResult
│
├── api/                       ── REST LAYER ──
│   ├── main.py                FastAPI app, router registration, CORS
│   ├── schemas.py             Pydantic request/response models
│   ├── storage.py             api_output/ directory layout, id generation
│   ├── db.py                  SQLite job store + legacy JSON migration
│   ├── routers/               health, pdf, ocr, review, diacritization, tts, pipeline, ai_jobs
│   └── ai_contract/           Backend ⇄ AI Engine contract
│       ├── config.py              Keycloak/backend settings
│       ├── auth.py                Incoming bearer-token validation (JWKS)
│       ├── job_store.py           Idempotency guard, status tracking
│       ├── backend_client.py      Token cache, download, 3-step upload, callbacks
│       └── processing.py          Background job execution
│
├── tests/                     pytest suite (no paid API calls)
├── api_output/                Runtime artifacts — gitignored
├── AUDIT_REPORT_AR.txt        Safety audit (Arabic), 2026-08-20
└── FUNCTIONAL_AUDIT_REPORT.txt  Functional audit — guard verification
```

---

## The safety philosophy

This is the part worth understanding before changing any code. Every design decision
below exists because an Arabic OCR correction that *looks* plausible from context is
frequently wrong — especially for proper names.

1. **PaddleOCR never supplies text.** It supplies bounding boxes only. LightOn is the
   OCR source of truth.
2. **Gemini Vision reads crops blind.** It is never told the original word, the proposed
   candidate, the sentence, or any context — so it cannot agree with what we want to hear.
   Similarity is computed in Python *after* the reading comes back.
3. **`UNCLEAR` is zero evidence, not weak evidence.** A confidence below 0.72, a reading
   that isn't a single Arabic word, or a reading equidistant from original and candidate
   all collapse to `UNCLEAR`, which cannot produce an `ACCEPT` for a letter change.
4. **Context alone can never change letters.** A high context score with weak visual
   consensus is capped below the accept threshold and downgraded to `REVIEW`.
5. **Corrections are applied to a verified character span**, not `str.replace(old, new, 1)`.
   A word is only replaced when its sentence occurs exactly once on the page *and* the
   token occurs exactly once in that sentence, matched on Arabic token boundaries — so
   `حسن` never matches inside `المحسن`. Ambiguous ⇒ `REVIEW`.
6. **The text-only final reviewer is OFF by default.** It has no image to check against,
   so it must not silently rewrite letters or names. Enable with
   `--enable-final-context-review` when you want it.
7. **Diacritization cannot alter letters.** A preservation check compares both texts with
   diacritics stripped; any difference in letters, punctuation, spacing or word order
   fails the stage rather than shipping the result.
8. **TTS validates its own output.** Audio duration is sanity-checked against word count;
   an abnormal chunk (silence or repetition) is regenerated once, and if it fails again
   the request fails rather than returning bad audio.
9. **Pages with ≥ 50% diacritics `REVIEW` skip TTS**, so no API budget is spent narrating
   text we don't trust.

Full detail: [docs/architecture.md](docs/architecture.md).
