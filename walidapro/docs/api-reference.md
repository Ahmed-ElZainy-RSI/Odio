# API reference

FastAPI application: `api/main.py` → `api.main:app`. Default bind `0.0.0.0:8080`.

Interactive documentation is generated automatically:

- **http://localhost:8080/docs** — Swagger UI
- **http://localhost:8080/redoc** — ReDoc
- **http://localhost:8080/openapi.json** — OpenAPI schema

CORS is configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`.
Tighten this in `api/main.py` before exposing the service outside a trusted network.

All error responses use FastAPI's default shape:

```json
{ "detail": "human-readable message" }
```

---

## Endpoint index

| Method | Path | Tag | Status |
|---|---|---|---|
| `GET` | `/health` | health | ✅ implemented |
| `POST` | `/api/v1/pdf/inspect` | 1. PDF Inspection | ✅ |
| `POST` | `/api/v1/pdf/render-page` | 1. PDF Inspection | ✅ |
| `POST` | `/api/v1/ocr/verify` | 2. OCR Verification | ✅ |
| `POST` | `/api/v1/review/final-text` | 3. Final Review | ⚠️ router empty |
| `POST` | `/api/v1/diacritization/page` | 4. Diacritization | ✅ |
| `POST` | `/api/v1/tts/generate` | 5. TTS | ✅ |
| `GET` | `/api/v1/tts/{tts_id}/audio` | 5. TTS | ✅ |
| `POST` | `/api/v1/pipeline/jobs` | 6. Full Pipeline | ⚠️ router empty |
| `GET` | `/api/v1/pipeline/jobs` | 6. Full Pipeline | ⚠️ router empty |
| `GET` | `/api/v1/pipeline/jobs/{job_id}` | 6. Full Pipeline | ⚠️ router empty |
| `POST` | `/api/jobs` | 7. Backend contract | ✅ route; ⚠️ `processing` empty |
| `GET` | `/api/jobs/{job_id}` | 7. Backend contract | ✅ (debug only) |

⚠️ = the route module exists but is 0 bytes in the working tree. The contract below is
reconstructed from `api/schemas.py` and `tests/`, which do specify it. See
[development.md § Known gaps](development.md#known-gaps-in-the-current-working-tree).

---

## Health

### `GET /health`

No parameters. Always:

```json
{ "status": "ok" }
```

---

## 1. PDF inspection

Both endpoints are pure local analysis — no external API calls, no cost. The uploaded PDF
is written to `api_output/uploads/`, processed, and **deleted in a `finally` block**.

### `POST /api/v1/pdf/inspect`

Detect which pages need OCR and which already carry a usable text layer.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | Must have a `.pdf` extension and be non-empty. |

**Response** `200` — `PDFInspectResponse`

```json
{
  "pdf": "/path/to/uploaded.pdf",
  "pages": 25,
  "text_layer_pages": 3,
  "ocr_pages": 22,
  "pdf_type": "MIXED_PDF",
  "page_results": [
    {
      "page": 1,
      "text_chars": 0,
      "visible_chars": 0,
      "arabic_chars": 0,
      "words": 0,
      "images": 1,
      "decision": "OCR_REQUIRED",
      "reason": "No text layer found"
    }
  ]
}
```

`pdf_type` is `TEXT_LAYER` | `OCR_REQUIRED` | `MIXED_PDF`.
`decision` is `TEXT_LAYER` | `OCR_REQUIRED`.

**Errors** — `400` missing/invalid/empty PDF · `502` PDF could not be parsed

```bash
curl -X POST http://localhost:8080/api/v1/pdf/inspect \
     -F "file=@book.pdf"
```

### `POST /api/v1/pdf/render-page`

Render a single page to PNG.

**Request** — `multipart/form-data` + query params

| Field | Type | Default | Notes |
|---|---|---|---|
| `file` | file | — | PDF. |
| `page` | int | `1` | 1-based. Must be ≥ 1. |
| `dpi` | int | `220` | Must be between 36 and 1200. |

**Response** `200` — raw `image/png` bytes.

**Errors** — `400` bad file / `page < 1` / `dpi` out of range · `404` page out of range ·
`502` render failed

```bash
curl -X POST "http://localhost:8080/api/v1/pdf/render-page?page=3&dpi=300" \
     -F "file=@book.pdf" -o page3.png
```

---

## 2. OCR verification

### `POST /api/v1/ocr/verify`

Full Stage 2: LightOn OCR → suspicious-word context review → bbox crop → multi-variant
visual re-OCR → Gemini visual reviewer (only when needed).

> Any letter change without independent visual evidence is left as-is and flagged for
> manual review rather than guessed.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | PDF. |
| `pages` | string | no | `"3"` \| `"1-16"` \| `"1,3,5"` \| `"1-4,8-12"`. Omit for all pages. |
| `dpi` | int | no | Defaults to `OCR_DPI` env, else 220. |

**Response** `200` — `OCRVerifyResponse`

```json
{
  "request_id": "a1b2c3d4e5f6a7b8",
  "report": {
    "pages": 5,
    "total_words": 612,
    "suspicious_words": 14,
    "accepted": 3,
    "rejected": 4,
    "manual_review": 7
  },
  "original_text": "<!-- page 1 -->\n\n…",
  "corrected_text": "<!-- page 1 -->\n\n…"
}
```

Full artifacts (crops, `corrections.jsonl`, `verification_report.md`) are persisted under
`api_output/ocr/{request_id}/` — see
[running.md § run.py output](running.md#output).

**Errors** — `400` missing/invalid PDF · `502` pipeline failed (LightOn/Gemini/PaddleOCR)

> ⏱ This is a **synchronous, long-running** call — minutes per page. For whole books use
> the job endpoints instead.

```bash
curl -X POST http://localhost:8080/api/v1/ocr/verify \
     -F "file=@book.pdf" -F "pages=1-5" -F "dpi=300"
```

---

## 3. Final text review

> ⚠️ `api/routers/review.py` is empty in the working tree. The shape below is defined by
> `api/schemas.py` and asserted by `tests/test_validation.py`.

### `POST /api/v1/review/final-text`

Conservative text-only audit (Stage 3). No image is involved, so this stage may only make
small, obvious corrections — and will refuse rather than guess.

**Request** — `application/json` — `FinalReviewRequest`

```json
{ "text": "النص المصحح من مرحلة OCR" }
```

`text` must have `min_length=1`.

**Response** `200` — `FinalReviewResponse`

```json
{
  "original_text":    "…",
  "corrected_text":   "…",
  "changed":          true,
  "attempts":         1,
  "review_required":  false,
  "rejection_reason": null,
  "changed_words":    2,
  "changed_ratio":    0.013
}
```

`review_required: true` with a `rejection_reason` means the model's answer was too
aggressive to trust without visual evidence — the original text is returned unchanged.
Speaker/person labels are protected and can never be altered by this stage.

**Errors** — `422` empty or missing `text`

---

## 4. Diacritization

### `POST /api/v1/diacritization/page`

Read visual diacritics (تشكيل) from a page image and apply them to the verified text.

> Letters, punctuation, spacing and word order are **never** changed here — guaranteed by
> an internal preservation check. Only diacritic marks are replaced, and only for words
> with a confident image-to-text alignment (≥ 0.72) whose letters are unchanged after the
> visual read. Everything else keeps its original diacritics and is marked `REVIEW`.

**Request** — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `image` | file | yes | PNG/JPG of the page. Must match `corrected_text`. |
| `corrected_text` | form string | yes | The OCR-verified text for that same page. Must be non-blank. |

**Response** `200` — `DiacritizationResponse`

```json
{
  "final_text": "النَّصُّ المُشَكَّلُ",
  "total_words": 128,
  "review_words": 9,
  "words": [
    {
      "index": 0,
      "reference_word": "النص",
      "visual_word": "النَّصُّ",
      "bbox": [120, 340, 260, 392],
      "status": "OK",
      "crop_path": null,
      "paddle_text": "النص",
      "alignment_score": 0.97
    }
  ]
}
```

`status` is `OK` (visual diacritics applied) or `REVIEW` (kept as-is).

**Errors** — `400` invalid/empty image, blank `corrected_text` · `422` missing fields ·
`502` pipeline failed (PaddleOCR/Gemini)

```bash
curl -X POST http://localhost:8080/api/v1/diacritization/page \
     -F "image=@page_001.png" \
     -F "corrected_text=$(cat corrected_text.md)"
```

---

## 5. TTS

### `POST /api/v1/tts/generate`

Generate narrated Arabic audio.

**Request** — `application/json` — `TTSRequest`

| Field | Type | Default | Notes |
|---|---|---|---|
| `text` | string | — | Required, `min_length=1`. |
| `narrator_type` | string | `"female"` | `"female"` (Leda) or `"male"` (Orus). |
| `voice` | string \| null | `null` | Explicit Gemini voice name; overrides the narrator default. |
| `input_name` | string \| null | `null` | Label used in saved files/reports. Defaults to `"api_request"`. |

**Response** `201` — `TTSResponse`

```json
{
  "tts_id": "9f8e7d6c5b4a3210",
  "narrator_type": "female",
  "voice": "Leda",
  "model": "gemini-3.1-flash-tts-preview",
  "total_chunks": 4,
  "successful_chunks": 4,
  "failed_chunks": 0,
  "total_characters": 2431,
  "total_duration_seconds": 143.2,
  "audio_available": true,
  "audio_url": "/api/v1/tts/9f8e7d6c5b4a3210/audio"
}
```

Audio duration is sanity-checked against word count; an abnormally short (silence) or long
(repetition) chunk is regenerated once, and if it still fails the request fails rather than
returning bad audio.

**Errors** — `400` empty text or invalid `narrator_type` · `502` generation failed or the
quality gate rejected the audio · `503` `GEMINI_TTS_API_KEY` not configured

```bash
curl -X POST http://localhost:8080/api/v1/tts/generate \
     -H "Content-Type: application/json" \
     -d '{"text":"مَرْحَبًا بِالْعَالَمِ","narrator_type":"male"}'
```

### `GET /api/v1/tts/{tts_id}/audio`

Download previously generated audio. Serves
`api_output/tts/{tts_id}/final_audio.wav`.

**Response** `200` — `audio/wav`, filename `{tts_id}.wav`.
**Errors** — `404` no audio for that `tts_id`.

```bash
curl -O -J http://localhost:8080/api/v1/tts/9f8e7d6c5b4a3210/audio
```

---

## 6. Full-book pipeline jobs

> ⚠️ `api/routers/pipeline.py` is empty in the working tree. The shape below is defined by
> `api/schemas.py`, the `pipeline_jobs` table in `api/db.py`, and
> `tests/test_validation.py`.

Asynchronous equivalent of `run_full_pipeline.py`. Jobs are tracked in the SQLite
`pipeline_jobs` table (`api_output/app.db`) with columns `job_id`, `status`,
`pages_requested`, `dpi`, `enable_final_context_review`, `error`, `created_at`,
`updated_at`.

### `POST /api/v1/pipeline/jobs`

**Request** — `multipart/form-data`: `file` (PDF, required), `pages` (selector),
`dpi`, `enable_final_context_review`.

`pages` is parsed **before** any processing or threading starts, so an invalid selector
fails fast with `400`.

**Response** `201`/`202` — `PipelineJobCreateResponse`

```json
{
  "job_id": "33be5d9c06d4404f",
  "pages": [1, 2, 3],
  "status": "queued",
  "status_url": "/api/v1/pipeline/jobs/33be5d9c06d4404f"
}
```

**Errors** — `400` non-PDF file or unparseable `pages` · `422` missing `file`

### `GET /api/v1/pipeline/jobs/{job_id}`

**Response** `200` — `PipelineJobStatusResponse`

```json
{
  "job_id": "33be5d9c06d4404f",
  "status": "running",
  "pages_requested": [1, 2, 3],
  "dpi": 220,
  "enable_final_context_review": false,
  "error": null,
  "report_summary": { }
}
```

`status` is `queued` | `running` | `done` | `error`.
**Errors** — `404` unknown `job_id`.

### `GET /api/v1/pipeline/jobs`

**Response** `200` — a list of `PipelineJobListItem`: `job_id`, `status`,
`pages_requested`.

Artifacts land under `api_output/jobs/{job_id}/` with the same per-page layout as
`run_full_pipeline.py` (`page_001/`, `book_report.json`, …). `PipelineJobTextResponse`
(`{ "text": … }`) exists in the schemas for retrieving a job's produced text.

---

## 7. Backend ⇄ AI Engine contract

Fully documented in [backend-contract.md](backend-contract.md). Summary:

### `POST /api/jobs`

Backend enqueues a job. Matches the integration spec exactly.

**Auth** — `Authorization: Bearer <token>` (Keycloak `client_credentials`, realm
`reading-first`), validated against the realm's JWKS. Bypass locally with
`AI_ENGINE_AUTH_DISABLED=true`.

**Request** — `application/json`

| Field | Type | Notes |
|---|---|---|
| `jobId` | string | Idempotency key. |
| `jobType` | string | `PAGES_IMG_AND_TEXT_EXTRACTION` or `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION`. |
| `fileId` | string | Backend file id, used to refresh an expired download URL. |
| `folderId` | string \| null | Upload destination. |
| `pathPrefix` | string \| null | Upload destination. |
| `sourceDownloadUrl` | string | Presigned S3 URL for the source file. |
| `pageNumber` | int \| null | **Required** for `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION`. |

**Response** — always `202 Accepted`, per spec. Work happens in the background; results are
pushed back via callbacks, not returned here.

```json
{ "jobId": "job-123", "status": "accepted", "duplicate": false }
```

A `jobId` already seen is acknowledged again with `202` and `"duplicate": true`, without
being reprocessed.

**Errors** — `400` unknown `jobType`, or `pageNumber` missing for an audio/timing job ·
`401` missing/invalid/expired token · `503` token validation unavailable

### `GET /api/jobs/{job_id}`

**Debug only — not part of the contract.** The backend learns job status from the callbacks
this service sends it; this endpoint exists so you can inspect local state while no live
backend is watching.

**Response** `200` — `jobId`, `jobType`, `status`, `payload`, plus whatever `extra` fields
the processing stage recorded.
**Errors** — `404` unknown `jobId`.
