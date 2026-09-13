# Configuration

All configuration is environment-based. Copy `.env.example` to `.env` and fill it in —
`python-dotenv` loads `.env` automatically at import time in both
`ocr_verifier/config.py` and `api/ai_contract/config.py`.

```bash
cp .env.example .env
```

`.env` is gitignored. `.env.example` is the tracked template.

---

## Minimum viable configuration

| Goal | Variables you must set |
|---|---|
| PDF inspection / rendering only | *(none — fully local)* |
| OCR verification | `LIGHTON_BASE_URL`, `REVIEWER_API_KEY`, `REVIEWER_MODEL` |
| + final text review | `FINAL_REVIEWER_MODEL` (falls back to reviewer key/URL) |
| + diacritization | same as OCR verification |
| + TTS | `GEMINI_TTS_API_KEY` (falls back to `REVIEWER_API_KEY`) |
| Backend job contract | `BACKEND_BASE_URL`, `TOKEN_URL`, `AI_ENGINE_CLIENT_SECRET` |

> **`REVIEWER_MODEL` has no default.** `ContextReviewer()` raises immediately if it is
> unset. This is intentional — silently falling back to some other model would change
> correction behaviour without anyone noticing.

---

## LightOn OCR

The primary OCR source.

| Variable | Default | Notes |
|---|---|---|
| `LIGHTON_BASE_URL` | `http://localhost:8000/v1` | Defaults to a **local** server. Point at your self-hosted or cloud LightOn endpoint. The client appends `/api/v3/parse`. |
| `LIGHTON_API_KEY` | `EMPTY` | Sent as `Authorization: Bearer <key>`. |
| `LIGHTON_MODEL` | `lightonai/LightOnOCR-2-1B` | Recorded in the OCR cache fingerprint. |
| `LIGHTON_MAX_SIDE` | `1540` | Images with a larger side are LANCZOS-downscaled before upload. Part of the cache fingerprint. |
| `LIGHTON_MAX_TOKENS` | `6500` | |

## Context reviewer (Stage 2)

Text-only suspicious-word detection inside OCR verification.

| Variable | Default | Notes |
|---|---|---|
| `REVIEWER_BASE_URL` | `http://localhost:8000/v1` | For Gemini use `https://generativelanguage.googleapis.com/v1beta/openai/` (OpenAI-compatible endpoint). |
| `REVIEWER_API_KEY` | `EMPTY` | |
| `REVIEWER_MODEL` | *(none — required)* | e.g. `gemini-3.1-flash-preview`. Raises if unset. |

This same configuration also drives `GeminiVisualReviewer` (blind crop reading) and the
diacritization vision calls.

## Final text-only reviewer (Stage 3, opt-in)

| Variable | Default | Notes |
|---|---|---|
| `FINAL_REVIEWER_API_KEY` | falls back to `REVIEWER_API_KEY` | |
| `FINAL_REVIEWER_BASE_URL` | falls back to `REVIEWER_BASE_URL` | |
| `FINAL_REVIEWER_MODEL` | `gemini-3.1-pro-preview` | A stronger model, because this stage has no image to check against. |

Off by default in `run_full_pipeline.py`; enable with `--enable-final-context-review`.

## Gemini TTS (Stage 5)

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_TTS_API_KEY` | falls back to `REVIEWER_API_KEY` | |
| `GEMINI_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | |
| `GEMINI_TTS_FEMALE_VOICE` | `Leda` | |
| `GEMINI_TTS_MALE_VOICE` | `Orus` | |

Output is 24 kHz mono WAV.

---

## Pipeline tuning

Defined in `ocr_verifier/config.py` as the frozen `Settings` dataclass (`SETTINGS`).

### Rendering & transport

| Variable | Default | Meaning |
|---|---|---|
| `OCR_DPI` | `220` | PDF render DPI. Higher = better diacritic detection, larger images, slower. Part of the OCR cache fingerprint. |
| `API_TIMEOUT` | `180` | Per-request timeout in seconds. |
| `API_RETRIES` | `3` | Retry attempts for LightOn (exponential backoff: 2, 4, 8 s). |

### Decision thresholds

These directly control how aggressive the corrector is. **Raising `ACCEPT_THRESHOLD` makes
the system more conservative** (more `REVIEW`, fewer automatic changes).

| Variable | Default | Meaning |
|---|---|---|
| `SUSPICION_THRESHOLD` | `0.75` | Below this, a word is considered suspicious. |
| `ACCEPT_THRESHOLD` | `0.88` | Fused score needed to auto-apply a correction. |
| `REVIEW_THRESHOLD` | `0.70` | Below this, a correction is `REJECT`ed outright. |

Guard rules in `decision.py` cap scores *relative* to these — e.g. a blocked correction is
capped at `REVIEW_THRESHOLD + 0.05`, and an `UNCLEAR` visual with changed letters is capped
at `ACCEPT_THRESHOLD - 0.01`. Changing the thresholds moves those caps with them.

### Cropping

| Variable | Default | Meaning |
|---|---|---|
| `CROP_PADDING_X` | `30` | Horizontal padding (px) around a word bbox. |
| `CROP_PADDING_Y` | `20` | Vertical padding (px). Context crops use ×4 these values. |

### PaddleOCR

| Variable | Default | Meaning |
|---|---|---|
| `PADDLE_DEVICE` | `cpu` | Set to `gpu` if you installed `paddlepaddle-gpu`. |

`bbox_detector.py` also sets two flags at import time, before PaddleOCR loads:

```python
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"
```

These avoid known Windows/CPU issues with oneDNN and the PIR API. Don't remove them
without testing on the target platform.

### Hard-coded thresholds (not env-configurable)

| Value | Location | Meaning |
|---|---|---|
| `0.72` | `visual_reviewer.py:871` | Gemini visual confidence below this ⇒ `UNCLEAR` |
| `0.72` | `word_diacritics.py:1085` (`MIN_DIACRITICS_ALIGNMENT`) | Alignment below this ⇒ `REVIEW`, no API call |
| `50%` | `run_full_pipeline.py` | Diacritics review rate at or above this ⇒ skip TTS for that page |
| `700` | `tts/pipeline.py:44` | TTS chunk size in characters |

---

## Backend ⇄ AI Engine contract

Read by `api/ai_contract/config.py` into the frozen `AIContractSettings` dataclass. See
[backend-contract.md](backend-contract.md) for how these are used.

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_BASE_URL` | `""` | Base URL of the backend AI Engine calls into. **Required** for `POST /api/jobs` to actually reach the backend. |
| `TOKEN_URL` | `""` | Keycloak token endpoint. **Required.** Shape: `http://host:port/realms/<realm>/protocol/openid-connect/token` |
| `AI_ENGINE_CLIENT_ID` | `reading-first-ocr-engine` | Keycloak client id. |
| `AI_ENGINE_CLIENT_SECRET` | `""` | **Required.** |
| `AI_ENGINE_JWT_ISSUER` | derived from `TOKEN_URL` | Everything before `/protocol/openid-connect/token`. Override only if the backend's tokens come from a different realm/gateway. |
| `AI_ENGINE_JWT_AUDIENCE` | `""` | Optional. If empty, audience verification is disabled — Keycloak `client_credentials` tokens don't always carry a predictable `aud`. |
| `AI_ENGINE_AUTH_DISABLED` | `false` | **Dev only.** Skips bearer-token validation on `POST /api/jobs`. |
| `AI_ENGINE_DEFAULT_NARRATOR` | `female` | Not in the spec's job payload; a fallback until the backend team confirms how narrator selection is passed. |
| `AI_ENGINE_DEFAULT_DPI` | `220` | Same — not in the job payload. |
| `AI_ENGINE_HTTP_TIMEOUT` | `120` | Timeout for calls into the backend. |
| `AI_ENGINE_TOKEN_SAFETY_SECONDS` | `30` | Refresh the cached token this many seconds before it actually expires. |

> ⚠️ **`AI_ENGINE_AUTH_DISABLED=true` is set in the shipped `.env.example`** so the endpoint
> can be exercised before real Keycloak/backend infrastructure exists. Validation is ON by
> default in code (`config.py` defaults to `"false"`); the example file overrides it. Turn
> it off in the example — or at minimum in your own `.env` — before any deployment.

The JWKS endpoint is derived, not configured: `{jwt_issuer}/protocol/openid-connect/certs`.

---

## Where output goes

| Path | Set by | Contents |
|---|---|---|
| `output/` | `run.py -o` | OCR verification artifacts |
| `full_pipeline_output/` | `run_full_pipeline.py -o` | Per-page book artifacts + `book_report.json` |
| `api_output/` | `api/storage.py` (fixed) | `jobs/`, `ocr/`, `diacritization/`, `tts/`, `uploads/`, `app.db` |

All three are gitignored. `api_output/` is **not** configurable — it's hard-coded relative
to the `api/` package.
