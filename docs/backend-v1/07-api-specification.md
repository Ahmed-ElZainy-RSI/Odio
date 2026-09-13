# API Specification

Two halves. **Part 1** is the API the AI Engine serves. **Part 2** is a client specification for
everything the AI Engine consumes, written so an implementer never has to open the PDF.

Normative rule: **the OpenAPI document for Part 1 is generated from the code, never hand-maintained.**
FastAPI + pydantic produce it from the same models that validate requests, so the schema and the
implementation cannot drift. This file is the prose contract; `/openapi.json` is the machine contract.

---

# Part 1 — Inbound: the AI Engine API

Base URL: `AI_Engine_BASE_URL` (example `https://localhost:9099`).
All request and response bodies are `application/json; charset=utf-8`.

## 1.1 `POST /api/jobs`

**REQUIREMENT** (REQ-JOB-01). The only functional endpoint in V1. Enqueues one job of either type.

**Purpose.** Accept a unit of work, deduplicate it by `jobId`, persist it durably, and return
immediately. No AI work occurs in this request.

**Authentication.** `Authorization: Bearer <JWT>` — REQ-AUTH-06. Validation mechanism is **`Q-01`**; the
PROPOSAL is Keycloak JWKS RS256 validation with an `azp`/`aud` allowlist
([12-security.md](12-security.md)).

### Request headers

| Header | Required | Notes |
|---|---|---|
| `Authorization` | **Yes** | `Bearer <token>`. Missing or malformed → `401`. |
| `Content-Type` | **Yes** | `application/json`. Anything else → `415`. |
| `X-Request-Id` | No | Echoed back and used as the log correlation id. Generated if absent. |

No path or query parameters.

### Request body

The body is a discriminated union on `jobType`.

| Field | Type | Required | Constraints | Validation rule |
|---|---|---|---|---|
| `jobId` | string | **Yes** | RFC 4122 GUID, any version | Must parse as a UUID. Compared case-insensitively; stored canonical-lowercase. Invalid → `VALIDATION_ERROR`. |
| `jobType` | enum | **Yes** | `PAGES_IMG_AND_TEXT_EXTRACTION` \| `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` | Exact string match. Unknown value → `VALIDATION_ERROR` (**not** silently accepted — an unknown type would otherwise be accepted with 202 and never run). |
| `fileId` | string | **Yes** | 1–128 chars | The Backend's file GUID. Job A: the PDF. Job B: the page's free-text file. Used for `get-download-url` (REQ-SRC-02). |
| `folderId` | string | **Yes** | 1–256 chars | Logical folder id. Passed through verbatim to `initialize-upload`. |
| `pathPrefix` | string | **Yes** | 1–512 chars | Physical folder id. Passed through verbatim. |
| `sourceDownloadUrl` | string | **Yes** | absolute URL, scheme `https` | **Host must be on `SOURCE_URL_ALLOWED_HOSTS`.** We fetch this server-side, so it is an SSRF vector — see [12-security.md](12-security.md). Off-allowlist → `VALIDATION_ERROR`. |
| `pageNumber` | integer | **Job B only** | `>= 1` | **ASSUMPTION A1**: 1-based, matching `src/pipeline/pages.py:4-5`. Present on Job A → `VALIDATION_ERROR`. Absent on Job B → `VALIDATION_ERROR`. See `Q-05`. |
| `qualityTier` | enum | No | `fast` \| `verified`, default `fast` | **PROPOSAL**, not in the PDF. Selects Pipeline S or the verified tier ([ADR-009](19-adrs.md)). Included so the choice is per-job rather than a redeploy. |

```json
// Job A
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "fileId": "b7d1e2c4-0a11-4c3e-9f2b-77c1a9e4d001",
  "folderId": "books/reading-first/grade-1",
  "pathPrefix": "s3-prefix/2026/09",
  "sourceDownloadUrl": "https://storage.example.com/uploads/b7d1e2c4.pdf?X-Amz-Signature=..."
}
```

```json
// Job B
{
  "jobId": "9c8b7a65-1234-4def-8abc-0011223344ff",
  "jobType": "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
  "fileId": "c1a2b3d4-5566-4778-99aa-bbccddeeff00",
  "folderId": "books/reading-first/grade-1",
  "pathPrefix": "s3-prefix/2026/09",
  "sourceDownloadUrl": "https://storage.example.com/text/p007.txt?X-Amz-Signature=...",
  "pageNumber": 7
}
```

### Response

**`202 Accepted`** for both a new job and a duplicate — REQ-JOB-01 and REQ-IDEM-01.

The PDF specifies no response body. **PROPOSAL** — return a small one, because a Backend engineer
debugging a stuck book needs to know whether their POST created work or hit the dedupe path, and a
`202` with an empty body cannot tell them:

```json
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "status": "QUEUED",
  "duplicate": false,
  "acceptedAt": "2026-09-04T10:15:32.881Z"
}
```

`duplicate: true` with `status` reflecting the existing job's current state is returned for a repeat
`jobId`. The Backend may ignore the body entirely; the contract is the status code.

### Status codes

| Code | When | Retry advice for the Backend |
|---|---|---|
| `202` | Accepted, new — or accepted, duplicate (REQ-IDEM-01) | Do not retry. |
| `400` | Body is not valid JSON | Do not retry; fix the caller. |
| `401` | Missing, malformed, expired or unverifiable token (REQ-AUTH-04) | Refresh the token and retry once. |
| `403` | Valid token from a client that is not on the allowlist | Do not retry; a configuration issue. |
| `409` | Same `jobId`, **materially different body** — see below | Do not retry; a caller bug. |
| `415` | `Content-Type` is not `application/json` | Do not retry. |
| `422` | Body is valid JSON but fails field validation | Do not retry. |
| `503` | Postgres unavailable — the job could not be persisted | **Retry with backoff.** This is the one case where the Backend's retry is essential. |

### The duplicate-with-different-body case

**PROPOSAL: `409 CONFLICT`.** REQ-IDEM-01 says a duplicate `jobId` returns 202, and it is written on the
assumption that the duplicate is *the same job re-delivered*. A same-`jobId`-different-body request is
not that; it is either a caller bug or an id collision, and both are worse if hidden.

Returning `202` would silently drop the new instruction while implying it was accepted — the worst
possible outcome. So: we compare a canonical hash of `(jobType, fileId, folderId, pathPrefix,
pageNumber)`. `sourceDownloadUrl` is **excluded** from the hash, deliberately, because a presigned URL
legitimately changes between retries (REQ-SRC-02 exists precisely because it expires). Same hash → 202
duplicate. Different hash → 409.

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "jobId already exists with a different payload",
    "details": { "jobId": "3f2504e0-...", "field": "pageNumber" }
  }
}
```

### Other semantics

| Concern | Behaviour |
|---|---|
| **Idempotency** | `jobs.job_id` is the PRIMARY KEY. Accept is `INSERT … ON CONFLICT (job_id) DO NOTHING`, executed in one statement so two concurrent identical POSTs cannot both create work (the race is analysed in [C-08](24-architecture-challenge.md)). |
| **Timeout** | The handler does DB work only. Server timeout **PROPOSAL** 10 s; exceeding it means Postgres is unhealthy, and the correct answer is `503`, not a long hang. |
| **Rate limiting** | **None in V1.** One trusted first-party caller (CONSTRAINT C1). If added later it must be generous enough not to interfere with the Backend's own retry storm during an incident. `Q-13`. |
| **Pagination** | Not applicable. |
| **Request size** | `PROPOSAL` cap 16 KB. The body is a handful of ids. |
| **AI/model errors** | **Cannot occur here.** No AI code path is reachable from this endpoint — the API process must not import any adapter (see the import rules in [05-target-architecture.md](05-target-architecture.md) §4). Model failures surface as page `/failed` callbacks, never as a response to this call. |

## 1.2 `GET /health`

**PROPOSAL** — operational necessity, not in the PDF.

Liveness. No authentication, no dependency checks. Returns `200` if the process is running.

```json
{ "status": "ok", "role": "api", "version": "1.0.0" }
```

Always `200` while the process is alive. A dependency check here would cause a Postgres blip to restart
every container, which is strictly worse than serving `503` from `/ready`.

## 1.3 `GET /ready`

**PROPOSAL.** Readiness. No authentication.

| Role | Checks |
|---|---|
| `api` | Postgres reachable (`SELECT 1`, 2 s timeout); JWKS cached or fetchable |
| `worker` | Postgres reachable; local model weights warmed (faster-whisper and, in the verified tier, PaddleOCR) |

```json
{
  "status": "ready",
  "role": "worker",
  "checks": {
    "postgres": { "ok": true, "latency_ms": 3 },
    "models": { "ok": true, "warmed": ["faster-whisper-small"] }
  }
}
```

`200` when every check passes, `503` with the same body otherwise.

**Deliberately not checked: Gemini and LightOn reachability.** A provider outage must not remove the API
from the load balancer, because accepting jobs into a durable queue is still exactly the right behaviour
during a provider incident. Provider health belongs in metrics and alerts, not in readiness.

## 1.4 `GET /metrics`

**PROPOSAL.** Prometheus text exposition. No authentication (bind to an internal network) or optional
bearer, per deployment. Metric catalogue in [13-observability.md](13-observability.md).

## 1.5 `GET /api/jobs/{jobId}` — optional, ops only

**PROPOSAL, and explicitly optional for V1.** The PDF does not ask for it and the Backend is not
expected to call it.

It exists for one reason: INVARIANT-1 failures are invisible from outside. When the Backend reports "book
X is stuck waiting for page 27", an engineer needs to see, in one request, which pages are terminal and
which are not. Without this, that answer requires database access.

**Authentication:** same as `POST /api/jobs`, plus `ops` scope, or restricted to an internal network.

```json
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "state": "RUNNING",
  "pagesCount": 30,
  "metadataSentAt": "2026-09-04T10:15:40.112Z",
  "pages": {
    "reportedCompleted": 26,
    "reportedFailed": 1,
    "running": 1,
    "pending": 2,
    "nonTerminal": [27, 28, 29]
  },
  "outboxPending": 0,
  "createdAt": "2026-09-04T10:15:32.881Z",
  "updatedAt": "2026-09-04T10:41:02.554Z"
}
```

`200` on success, `404 NOT_FOUND` for an unknown `jobId`. It returns no artifact bytes and no source URL.

## 1.6 Error envelope

Every non-2xx response from this API uses one shape. Taxonomy and HTTP mapping in
[14-error-model.md](14-error-model.md).

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "pageNumber is required for PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
    "details": { "field": "pageNumber" },
    "requestId": "01J8Z3K7QF2E9V0S6M4B1TX7YD"
  }
}
```

`message` is always safe to log and display. `details` never contains a token, a presigned URL, or a
stack trace.

---

# Part 2 — Outbound: client specification

Eight calls. Nothing beyond this list may be invented — see
[02-v1-requirements.md](02-v1-requirements.md) §4 for the verbatim spec extraction.

Shared retry policy for all of them (**PROPOSAL**, since the PDF specifies only that *the Backend*
retries):

| Response | Action |
|---|---|
| `2xx` | Success |
| `401` | Force a token refresh, retry **once**. Then terminal `AUTHENTICATION_ERROR`. |
| `408`, `429`, `5xx`, connection error, read timeout | Retry with exponential backoff + **full jitter**, max 5 attempts, ≤60 s total per call. Honour `Retry-After` when present. |
| `400`, `403`, `404`, `409`, `415`, `422` | **Never retry.** Terminal. |

Two things this policy exists to prevent, both observed in the current code:
`walidapro/ocr_verifier/lighton_ocr.py:180` retries an auth failure identically to a 429, and its
`time.sleep(2**attempt)` at `:197` has no jitter and no `Retry-After` handling, giving a worst case of
546 s of blocked worker per call.

## 2.1 Keycloak — obtain the Backend token

**REQ-AUTH-01.** Called lazily, whenever the cached token has ≤30 s of life left.

```http
POST {TOKEN_URL}
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id={AI_ENGINE_CLIENT_ID}&client_secret={AI_ENGINE_CLIENT_SECRET}
```

```json
// 200
{ "access_token": "eyJhbGciO...", "expires_in": 300, "token_type": "Bearer" }
```

| Aspect | Detail |
|---|---|
| Auth | The credentials *are* the auth |
| Lifecycle position | Before any Backend call; never before an S3 presigned call |
| Timeout | connect 3 s, read 5 s |
| Retry | Per the shared policy, but **not** on 401 (a 401 here means bad credentials, which is terminal and must page a human) |
| Caching | In-memory, per process, **single-flight** so N concurrent page tasks trigger one refresh, not N (REQ-AUTH-02/03) |
| Never | log the `access_token`, persist it, or send it to a non-Backend host |

## 2.2 S3 — download the source

**REQ-SRC-01.**

```http
GET {sourceDownloadUrl}
--> 200 <bytes>
```

| Aspect | Detail |
|---|---|
| Auth | **None.** The URL is presigned. Sending our Backend token here would leak it to the storage host. |
| Lifecycle position | First worker action |
| Timeout | connect 5 s, read 60 s, total 300 s |
| Retry | Shared policy. A `403` here is **special**: for presigned URLs it usually means *expired*, not *forbidden*, so it triggers §2.3 rather than being terminal. |
| Validation | Stream to disk with a hard byte cap; verify magic bytes; verify content type. See [12-security.md](12-security.md). |
| Never | log the URL — the signature is a credential |

## 2.3 Backend — refresh the download URL

**REQ-SRC-02.**

```http
GET {BACKEND_BASE_URL}/api/files/{fileId}/get-download-url
Authorization: Bearer {backend-token}
--> 200 { "url": "https://storage.example.com/...?X-Amz-Signature=..." }
```

| Aspect | Detail |
|---|---|
| Lifecycle position | Only after a presigned GET fails as expired |
| Timeout | connect 3 s, read 10 s |
| Retry | Shared policy |
| Failure | After one refresh-and-retry cycle fails, `SOURCE_UNAVAILABLE` → whole-job `/failed` (REQ-CB-05) |
| Loop guard | At most **one** refresh per download attempt. Without this, an expired-URL bug becomes an infinite refresh loop. |

## 2.4 Backend — initialize upload

**REQ-UPL-01.** Step 1 of 3, once per file.

```http
POST {BACKEND_BASE_URL}/api/files/initialize-upload
Authorization: Bearer {backend-token}
Content-Type: application/json

{ "fileName": "p007.jpg", "contentType": "image/jpeg",
  "sizeInBytes": 164231, "folderId": "{job.folderId}", "pathPrefix": "{job.pathPrefix}" }
```

```json
// 200
{ "fileId": "e5f6a7b8-1111-4222-8333-444455556666",
  "upload": { "url": "https://storage.example.com/put/...?X-Amz-Signature=...",
              "requiredHeaders": { "Content-Type": "image/jpeg", "Content-Length": "164231" } } }
```

| Aspect | Detail |
|---|---|
| `contentType` | Exactly one of `image/jpeg`, `application/json`, `audio/wav`, `text/plain` (REQ-UPL-04) |
| `sizeInBytes` | The exact byte length of what we are about to PUT. Must match `Content-Length`. |
| `fileName` | **ASSUMPTION A4** — chosen by us, not semantically interpreted. PROPOSAL `p{page:03d}.{jpg,txt,wav,json}`. |
| `folderId` / `pathPrefix` | Passed through verbatim from the job body |
| Timeout | connect 3 s, read 15 s |
| Retry | Shared policy. **Caveat:** a retry may mint a second `fileId` for the same logical file, orphaning the first. Whether that is harmful is `Q-02`. Until answered, we retry but record every minted `fileId` so orphans are at least traceable. |

## 2.5 S3 — PUT the artifact

**REQ-UPL-02.** Step 2 of 3.

```http
PUT {upload.url}
Content-Type: {upload.requiredHeaders["Content-Type"]}
Content-Length: {upload.requiredHeaders["Content-Length"]}

<bytes>
--> 200
```

| Aspect | Detail |
|---|---|
| Headers | **Exactly** the returned `requiredHeaders`, verbatim. Do not add, reorder or normalize — a presigned PUT signature covers them. |
| Auth | None (ASSUMPTION A5) |
| Timeout | connect 5 s, read 120 s (a p90 WAV is 1.8 MB; the max observed is 8.9 MB) |
| Retry | Shared policy. A `403` here means the upload ticket expired → go back to §2.4 for a fresh ticket, at most once. |

## 2.6 Backend — confirm upload

**REQ-UPL-03.** Step 3 of 3. The `artifacts` ledger row is written **after** this returns 200; only then
is the `fileId` usable in a callback.

```http
POST {BACKEND_BASE_URL}/api/files/{fileId}/confirm-upload
Authorization: Bearer {backend-token}
--> 200
```

| Aspect | Detail |
|---|---|
| Timeout | connect 3 s, read 15 s |
| Retry | Shared policy — which means it **may be called twice**. Whether that is safe is `Q-02`, and it is one of the most important questions for the Backend team. |
| Body | The PDF shows none. We send none. |

## 2.7 Backend — job metadata

**REQ-META-01.** Sent exactly once per Job A, **before** any page artifact upload.

```http
PATCH {BACKEND_BASE_URL}/api/jobs/{jobId}/metadata
Authorization: Bearer {backend-token}
Content-Type: application/json

{ "pagesCount": 30 }
--> 200
```

| Aspect | Detail |
|---|---|
| Lifecycle position | Immediately after the page count is known; **gates all page work** |
| Timeout | connect 3 s, read 15 s |
| Retry | Shared policy, via the outbox |
| Failure | If this cannot be delivered, no page work starts and the job escalates to `/api/jobs/{jobId}/failed`. Starting page work without it would make the Backend receive page callbacks for a job whose page count it does not know. |
| Job B | Not sent. Job B covers a single supplied page. See `Q-06`. |

## 2.8 Backend — page completed

**REQ-CB-01/02/03.** Sent only after **both** of the page's upload cycles have confirmed.

```http
PATCH {BACKEND_BASE_URL}/api/jobs/{jobId}/{pageNumber}/completed
Authorization: Bearer {backend-token}
Content-Type: application/json

{ "artifacts": [ { "fileId": "e5f6a7b8-...", "artifactType": "Image" },
                 { "fileId": "f6a7b8c9-...", "artifactType": "Text"  } ] }
--> 200
```

Job B sends `"Audio"` then `"JSON"`. The four `artifactType` values are exact, capitalized strings.

| Aspect | Detail |
|---|---|
| Ordering | Artifacts listed in production order (image then text; audio then json), matching REQ-UPL-05 |
| Delivery | Via the outbox, at-least-once |
| Idempotency | The Backend may see a duplicate after a network-level retry. `Q-03`. |
| Timeout | connect 3 s, read 15 s |

## 2.9 Backend — page failed

**REQ-CB-04.**

```http
PATCH {BACKEND_BASE_URL}/api/jobs/{jobId}/{pageNumber}/failed
Authorization: Bearer {backend-token}
Content-Type: application/json

{ "reason": "AI_INFERENCE_ERROR: OCR provider returned 429 after 5 attempts" }
--> 200
```

`reason` rules (**PROPOSAL**, since the format is unspecified — `Q-14`): ≤512 characters, a stable
machine-readable error code prefix followed by a human tail, and **never** a presigned URL, a token, an
API key or a stack trace. Full examples in [14-error-model.md](14-error-model.md).

## 2.10 Backend — job failed

**REQ-CB-05.**

```http
PATCH {BACKEND_BASE_URL}/api/jobs/{jobId}/failed
Authorization: Bearer {backend-token}
Content-Type: application/json

{ "reason": "SOURCE_INVALID: downloaded file is not a parseable PDF" }
--> 200
```

Sent when the job cannot proceed at all — source unfetchable or unparseable, page count
indeterminable, or the metadata PATCH undeliverable. After this, no page callbacks are sent. The
reconciler also uses this as its last-resort escalation.

---

# Part 3 — Questions for the Backend team

Each is a real blocker or a real risk, not a nitpick. Cross-referenced in
[22-open-questions.md](22-open-questions.md).

| # | Question | Why we need it |
|---|---|---|
| `Q-01` | **How should we validate the inbound `{ai-engine-bearer-token}`?** Which Keycloak client does the Backend present, what `aud`/`azp` should we expect, is RS256 correct, and where is the JWKS endpoint? | The PDF gives us credentials for calling *you*, and says inbound calls carry a bearer token, but never says how we verify it. Without this we cannot implement authentication at all. **Largest single ambiguity in the spec.** |
| `Q-02` | **Is `POST /api/files/{fileId}/confirm-upload` idempotent?** | We must retry on 5xx/timeout. If a double confirm is an error we need a different strategy. Related: does a retried `initialize-upload` orphan the first `fileId`? |
| `Q-03` | **Is `PATCH /api/jobs/{jobId}/{pageNumber}/completed` idempotent?** And `/failed`? | At-least-once delivery means you will occasionally see a duplicate. If that double-counts pages, INVARIANT-1 breaks from your side. |
| `Q-05` | **Is `pageNumber` 1-based?** And is it the physical PDF page index? | We assume 1-based from your `pagesCount: 30` / "waiting for number 30 page" example. A one-off error here mis-labels every artifact in every book. |
| `Q-06` | **Is `pagesCount` always the full PDF page count?** Could a Job A ever cover a subrange? | It determines what INVARIANT-1 is quantified over. |
| `Q-08` | **What is the presigned URL TTL, for both download and upload? What is the maximum `sizeInBytes`?** | We observed an 8.9 MB WAV on disk. We need to size our retry windows and know whether to reject large audio before uploading. |
| `Q-08b` | **One audio file per page, or could you take two?** | The existing pipeline synthesizes **two** narrators per page (female and male, `walidapro/run_full_pipeline.py:1057-1130`) while your `completed` payload carries a single `Audio` artifact. |
| `Q-09` | **Is `audio/wav` required, or merely permitted?** | WAV is ~1.0 MB/page median. `README.md:186-190`'s own scale table puts 1,000 books at 36 GB WAV versus 12 GB MP3. If you can accept MP3 we would like to. |
| `Q-04` | **Is the `Text` artifact raw OCR text, or final corrected + diacritized reading text?** | We assume final reading text, since it is what the reader displays and what Job B feeds to TTS — undiacritized Arabic is mispronounced. |
| `Q-10` | **What does the reader app need from the timing JSON?** In particular, does it need bounding boxes? | You never specify the schema. We propose a self-contained one ([10-data-contracts.md](10-data-contracts.md)). Boxes exist in Job A's data and have **no artifact slot at all** in the current contract. |
| `Q-14` | **Is `reason` free text for humans, or do you parse it?** | Determines whether we can safely change wording later. |
| `Q-15` | **TLS posture.** Your examples are `https://localhost:9099` / `https://localhost:6060` but `token_URL` is `http://localhost:9090`. Is the token endpoint plain HTTP in production? Do you want mTLS between the services? | Sending `client_secret` over plain HTTP in production would be unacceptable. |
| `Q-16` | **What happens on your side if we PATCH `completed` for a page number greater than the `pagesCount` we sent, or one we already reported?** | Defensive: it tells us how much our own guardrails matter. |
