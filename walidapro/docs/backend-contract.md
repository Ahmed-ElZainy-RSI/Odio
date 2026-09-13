# Backend ⇄ AI Engine contract

Implements *"Steps For Communication Between AI Engine To Backend And Vice Versa"*.

Two services talk to each other:

- **Backend** — owns files, folders and job records. Calls **into** AI Engine to enqueue work.
- **AI Engine** (this service) — does OCR / diacritization / TTS. Calls **into** the backend
  to download sources, upload artifacts, and report progress.

Both directions authenticate with Keycloak `client_credentials` tokens from the
`reading-first` realm.

**Modules:** `api/routers/ai_jobs.py`, `api/ai_contract/{config,auth,job_store,backend_client,processing}.py`

---

## Flow

```
 BACKEND                                AI ENGINE                         S3
    │                                       │                              │
    │ POST /api/jobs  (Bearer token)        │                              │
    ├──────────────────────────────────────►│                              │
    │              202 Accepted             │  validate token (JWKS)       │
    │◄──────────────────────────────────────┤  claim_job() ── idempotent   │
    │                                       │  submit() to thread pool     │
    │                                       │                              │
    │                                       │  GET sourceDownloadUrl       │
    │                                       ├─────────────────────────────►│
    │  (403/410 ⇒ refresh)                  │                              │
    │◄──────────────────────────────────────┤  GET /api/files/{id}/get-download-url
    │                                       │                              │
    │  PATCH /api/jobs/{id}/metadata        │  ── process pages ──         │
    │◄──────────────────────────────────────┤                              │
    │       { "pagesCount": N }             │                              │
    │                                       │                              │
    │  POST /api/files/initialize-upload    │                              │
    │◄──────────────────────────────────────┤                              │
    │       { fileId, upload:{url, requiredHeaders} }                      │
    ├──────────────────────────────────────►│  PUT artifact                │
    │                                       ├─────────────────────────────►│
    │  POST /api/files/{fileId}/confirm-upload                             │
    │◄──────────────────────────────────────┤                              │
    │                                       │                              │
    │  PATCH /api/jobs/{id}/{page}/completed│                              │
    │◄──────────────────────────────────────┤                              │
    │       { "artifacts": [...] }          │                              │
```

On failure the corresponding call is `PATCH /api/jobs/{id}/{page}/failed` (one page) or
`PATCH /api/jobs/{id}/failed` (whole job), each with `{ "reason": "…" }`.

---

## Inbound: `POST /api/jobs`

### Authentication

`api/ai_contract/auth.py` → `verify_ai_engine_token()`, a FastAPI dependency.

Verifies the bearer token's **signature** (against the realm's JWKS), **issuer** and
**expiry**.

- JWKS URL is derived, not configured: `{jwt_issuer}/protocol/openid-connect/certs`
- `jwt_issuer` defaults to `TOKEN_URL` minus the `/protocol/openid-connect/token` suffix
- **Audience is only checked when `AI_ENGINE_JWT_AUDIENCE` is set** — Keycloak
  `client_credentials` tokens don't reliably carry a predictable `aud` claim
- Algorithm is pinned to `RS256`
- The `PyJWKClient` is created lazily and cached in a module global

| Failure | Response |
|---|---|
| Missing / malformed `Authorization` header | `401 Missing or malformed Authorization header.` |
| Expired token | `401 Token expired.` |
| Invalid token | `401 Invalid token: …` |
| JWKS unreachable, issuer unconfigured | `503 Token validation unavailable: …` |

**Dev bypass.** `AI_ENGINE_AUTH_DISABLED=true` short-circuits all of the above and returns
`{"sub": "dev-mode", "auth_disabled": True}`. The code default is `false` (validation ON),
but **the shipped `.env.example` sets it to `true`** so the endpoint can be exercised before
real infrastructure exists. Make sure it is off in any deployed environment.

### Request

```json
{
  "jobId": "job-123",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "fileId": "file-1",
  "folderId": "folder-1",
  "pathPrefix": "books/1",
  "sourceDownloadUrl": "https://s3.../source.pdf?X-Amz-…",
  "pageNumber": null
}
```

### Job types

| `jobType` | Source | Produces | `pageNumber` |
|---|---|---|---|
| `PAGES_IMG_AND_TEXT_EXTRACTION` | PDF | Per page: a page **Image** + extracted **Text** | not used |
| `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` | free-text file | **Audio** (WAV) + timing **JSON** | **required** |

An unknown `jobType` ⇒ `400`. A missing `pageNumber` on the audio job ⇒ `400`.

### Idempotency

`job_store.claim_job()` inserts into the `ai_contract_jobs` SQLite table under a
`threading.Lock`. It returns `True` only for a genuinely new `jobId`.

- new ⇒ background work is submitted, response `{"duplicate": false}`
- already seen ⇒ **nothing is reprocessed**, response `{"duplicate": true}`

Both cases return `202 Accepted`, as the spec requires.

### Response

Always `202`. The endpoint never returns results — those arrive at the backend through the
callbacks below.

---

## Outbound: `BackendClient`

`api/ai_contract/backend_client.py`. A module-level singleton `backend_client`.

### Token acquisition

```
POST {TOKEN_URL}
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
client_id={AI_ENGINE_CLIENT_ID}
client_secret={AI_ENGINE_CLIENT_SECRET}
```

The token is **cached** behind a `threading.Lock` and refreshed only when missing or within
`AI_ENGINE_TOKEN_SAFETY_SECONDS` (30 s) of expiry — the spec explicitly asks not to hit the
token endpoint before every request. Expiry is tracked with `time.monotonic()`, so a system
clock change can't confuse it.

Missing `TOKEN_URL` or `AI_ENGINE_CLIENT_SECRET` raises `BackendClientError`.

### Download

```python
get_fresh_download_url(file_id) -> str
    GET {BACKEND_BASE_URL}/api/files/{file_id}/get-download-url  → { "url": … }

download_file(url, file_id) -> bytes
```

`download_file()` first tries the URL it was given. **On `403` or `410`** — the common
presigned-URL expiry signals — it asks the backend for a fresh URL **once** and retries.
This is covered by `test_pages_extraction_job_download_url_refresh_on_expiry`.

### Upload — the 3-step cycle

```python
upload_file(file_name, content_type, content, folder_id, path_prefix) -> file_id
```

1. **Initialize** — `POST {BACKEND_BASE_URL}/api/files/initialize-upload`
   ```json
   { "fileName": "…", "contentType": "…", "sizeInBytes": 12345,
     "folderId": "…", "pathPrefix": "…" }
   ```
   → `{ "fileId": "…", "upload": { "url": "…", "requiredHeaders": { … } } }`

2. **PUT to S3** — `PUT upload.url` with `upload.requiredHeaders` and the raw bytes.
   Note this is a **direct S3 PUT**, not a call to the backend.

3. **Confirm** — `POST {BACKEND_BASE_URL}/api/files/{fileId}/confirm-upload`

Returns the backend `fileId`, which then goes into an `artifacts` entry.

### Status callbacks

| Method | Path | Body |
|---|---|---|
| `PATCH` | `/api/jobs/{job_id}/metadata` | `{ "pagesCount": N }` |
| `PATCH` | `/api/jobs/{job_id}/{page}/completed` | `{ "artifacts": [ … ] }` |
| `PATCH` | `/api/jobs/{job_id}/{page}/failed` | `{ "reason": "…" }` |
| `PATCH` | `/api/jobs/{job_id}/failed` | `{ "reason": "…" }` |

`metadata` is sent **once** per job, as soon as the page count is known.

### Artifact types

Asserted by `tests/test_ai_contract.py`:

| Job type | `artifactType` values per page |
|---|---|
| `PAGES_IMG_AND_TEXT_EXTRACTION` | `{"Image", "Text"}` |
| `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` | `{"Audio", "JSON"}` |

---

## Local job state

Two SQLite tables in `api_output/app.db` (`api/db.py`). WAL journal mode, 30 s busy timeout.

**`ai_contract_jobs`** — `job_id` (PK), `job_type`, `status`, `payload` (JSON),
`extra` (JSON), `created_at`, `updated_at`.

**`pipeline_jobs`** — for the `/api/v1/pipeline/jobs` endpoints.

`init_db()` runs at import time and also performs a **one-time migration** from the
pre-SQLite file-based stores, so upgrading doesn't lose jobs:

- `api_output/jobs/*/job_meta.json` → `pipeline_jobs`
- `api_output/ai_jobs/*.json` → `ai_contract_jobs`

Both use `INSERT OR IGNORE` and skip unparseable files, so the migration is safe to
re-run.

`GET /api/jobs/{job_id}` exposes this state. It is a **debug endpoint, not part of the
contract** — the backend is meant to learn status from callbacks.

---

## Testing without a backend

`tests/test_ai_contract.py` proves the contract plumbing (auth, token caching, idempotency,
download refresh, the 3-step upload, and every callback) with:

- `requests_mock` standing in for Keycloak, the backend, and S3
- `_FakeOCRPipeline` and `_FakeTTSPipeline` monkeypatched over
  `processing.OCRVerificationPipeline` / `processing.TTSPipeline`

That substitution is deliberate — this file verifies the *contract*, not OCR/TTS quality,
which needs real paid keys and belongs in a separate opt-in integration test.

Covered cases:

| Test | Verifies |
|---|---|
| `test_enqueue_returns_202` | Happy-path acceptance |
| `test_enqueue_same_jobid_twice_is_idempotent` | Duplicate `jobId` is not reprocessed |
| `test_enqueue_unknown_job_type_rejected` | `400` on bad `jobType` |
| `test_audio_job_requires_page_number` | `400` when `pageNumber` is missing |
| `test_missing_authorization_header_rejected` | `401` with auth enabled |
| `test_malformed_bearer_token_rejected` | `401` on a malformed token |
| `test_pages_extraction_job_full_flow` | Token request, one `metadata` call, one `completed` call, artifacts = `{Image, Text}`, no `failed` calls |
| `test_pages_extraction_job_download_url_refresh_on_expiry` | Expired presigned URL is refreshed and retried |
| `test_pages_extraction_job_page_error_reported_as_failed` | A broken page reports `failed` |
| `test_audio_timing_job_full_flow` | Artifacts = `{Audio, JSON}` on the right page number |

> ⚠️ `api/ai_contract/processing.py` is empty in the working tree, so these tests cannot
> currently run. See [development.md](development.md#known-gaps-in-the-current-working-tree).

---

## Deployment checklist

- [ ] `AI_ENGINE_AUTH_DISABLED` is **unset or `false`** (check `.env.example` too — it ships as `true`)
- [ ] `AI_ENGINE_CLIENT_SECRET` set, and not committed
- [ ] `BACKEND_BASE_URL` uses **HTTPS** with a valid certificate (`.env.example` points at `https://localhost:6060`)
- [ ] `TOKEN_URL` points at the real Keycloak realm (`.env.example` points at `http://localhost:9090`)
- [ ] `AI_ENGINE_JWT_AUDIENCE` set if the backend's tokens carry a stable `aud` — otherwise audience checking is off
- [ ] CORS in `api/main.py` narrowed from `allow_origins=["*"]`
- [ ] `api_output/` is on persistent storage — it holds `app.db` and all job artifacts
- [ ] Narrator/DPI selection confirmed with the backend team: `AI_ENGINE_DEFAULT_NARRATOR` and `AI_ENGINE_DEFAULT_DPI` are **fallbacks**, because the spec's job payload doesn't carry these fields
