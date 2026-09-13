# API reference

Base URL: `AI_ENGINE_BASE_URL` (default port `9099`). All bodies are
`application/json; charset=utf-8`.

The authoritative machine contract is `GET /openapi.json`, generated from the same pydantic
models that validate requests — so the schema and the implementation cannot drift. This
document is the prose companion.

For the full contract as designed, including the outbound half, see
[`../../docs/backend-v1/07-api-specification.md`](../../docs/backend-v1/07-api-specification.md).

---

## `POST /api/jobs`

The only endpoint in the V1 contract. Enqueues one job and returns immediately; no AI work
happens in this request.

### Headers

| Header | Required | Notes |
|---|---|---|
| `Authorization` | see below | `Bearer <token>`. Enforced only when `AUTH_ENFORCE=true` — see [Authentication](#authentication). |
| `Content-Type` | yes | `application/json`. |
| `X-Request-Id` | no | Echoed back and used as the log correlation id. Generated if absent. |

### Body

Discriminated on `jobType`. Unknown fields are **rejected**, because silently ignoring one
hides a contract mismatch.

| Field | Type | Job A | Job B | Constraints |
|---|---|:---:|:---:|---|
| `jobId` | GUID | ✔ | ✔ | RFC 4122. Stored canonical-lowercase. **This is the idempotency key.** |
| `jobType` | enum | `PAGES_IMG_AND_TEXT_EXTRACTION` | `PAGE_AUDIO_AND_TEXT_TIMING_GENERATION` | exact match |
| `fileId` | string | the PDF | the page's text file | 1–128 chars |
| `folderId` | string | ✔ | ✔ | 1–256 chars, no `..`, no leading separator, no NUL |
| `pathPrefix` | string | ✔ | ✔ | 1–512 chars, same restrictions |
| `sourceDownloadUrl` | URL | ✔ | ✔ | absolute; `https` required; host must be on `SOURCE_URL_ALLOWED_HOSTS` |
| `pageNumber` | int | **rejected** | **required** | `>= 1` |
| `qualityTier` | enum | optional | optional | `fast` \| `verified`. **PROPOSAL**, not in the spec. Downgraded to `fast` unless `ENABLE_VERIFIED_TIER=true`. |

```json
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "fileId": "b7d1e2c4-0a11-4c3e-9f2b-77c1a9e4d001",
  "folderId": "books/reading-first/grade-1",
  "pathPrefix": "s3-prefix/2026/09",
  "sourceDownloadUrl": "https://storage.internal/uploads/b7d1e2c4.pdf?X-Amz-Signature=..."
}
```

### Responses

```json
// 202 Accepted — new, and also for a duplicate
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "status": "QUEUED",
  "duplicate": false,
  "acceptedAt": "2026-09-07T10:15:32.881Z"
}
```

| Code | When | Should the caller retry? |
|---|---|---|
| `202` | Accepted — new, **or** a duplicate `jobId` with the same payload | No |
| `400` | Body is not valid JSON | No |
| `401` | Missing, malformed or unverifiable token | Refresh and retry once |
| `403` | Valid token from a client not on the allowlist | No — configuration |
| `409` | Same `jobId`, **materially different** payload | No — caller bug |
| `415` | `Content-Type` is not `application/json` | No |
| `422` | Body failed field validation | No |
| `503` | The job could not be persisted | **Yes, with backoff** |

### Idempotency, precisely

The accept is a single atomic insert-if-absent, not a check-then-insert, so 20 concurrent
POSTs with the same `jobId` produce exactly one job and twenty `202`s
(`test_concurrent_identical_accepts_create_exactly_one_job`).

A same-`jobId`-different-payload request returns `409` rather than `202`. Returning `202`
would silently drop the new instruction while implying acceptance, which is the worst
available outcome.

**`sourceDownloadUrl` is excluded from the comparison hash.** A presigned URL legitimately
changes between retries — the whole reason `get-download-url` exists is that these expire —
so including it would turn every ordinary Backend retry into a spurious conflict. Asserted
by `test_duplicate_with_a_refreshed_source_url_is_not_a_conflict`.

### Why `503` and not `202` when the store is down

Returning `202` without persisting would be a lie: the Backend would stop retrying a job
that will never run, and the book would stall silently and permanently. A `503` makes the
Backend retry, which is exactly right. Asserted by
`test_postgres_down_returns_503_not_202`.

---

## `GET /api/jobs/{jobId}`

**PROPOSAL, optional, not part of the V1 contract.** It exists because an INVARIANT-1
failure is invisible from outside: when the Backend says "book X is stuck waiting for page
27", this answers it in one request instead of requiring database access.

```json
{
  "jobId": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "state": "RUNNING",
  "qualityTier": "fast",
  "pagesCount": 30,
  "pageNumber": null,
  "metadataSentAt": "2026-09-07T10:15:40.112Z",
  "pages": {
    "reportedCompleted": 26,
    "reportedFailed": 1,
    "running": 1,
    "pending": 2,
    "nonTerminal": [27, 28, 29]
  },
  "createdAt": "2026-09-07T10:15:32.881Z",
  "updatedAt": "2026-09-07T10:41:02.554Z"
}
```

`404` for an unknown id. Returns **no artifact bytes and no source URL** — the latter is a
credential, asserted by `test_job_status_never_leaks_the_source_url`.

---

## `GET /health`

Liveness. **Always `200` while the process is alive, with no dependency checks.** Checking
one here would restart every container during a brief database blip, which is strictly
worse than serving `503` from `/ready`.

```json
{ "status": "ok", "role": "api", "version": "0.1.0" }
```

## `GET /ready`

Readiness. `200` when every check passes, `503` with the same body otherwise.

```json
{
  "status": "ready",
  "role": "api",
  "checks": {
    "job_store": { "ok": true, "detail": null },
    "inbound_auth": { "ok": true, "detail": "not enforced (blocked on Q-01)" }
  }
}
```

**Provider reachability is deliberately not checked.** During a Gemini or LightOn outage,
accepting jobs into a durable queue is still the correct behaviour — removing the API from
the load balancer would make the Backend's POSTs fail and pile up its retries. Provider
health belongs in alerts.

## `GET /metrics`

Prometheus text exposition. Falls back to an in-process registry when `prometheus-client`
is not installed, so the metric **names** stay authoritative either way. Catalogue in
[08-operations.md](08-operations.md).

---

## `GET /ops/budget`

How much of each paid provider's allowance is left in the current window.

```json
{
  "windows": [
    {
      "provider": "gemini",
      "used": 10,
      "remaining": 0,
      "maxCalls": 10,
      "windowStart": "2026-09-07T00:00:00Z",
      "windowReset": "2026-09-08T00:00:00Z"
    }
  ]
}
```

`{"windows": []}` means no provider is metered — the default. Not an error.

This exists because the failure it describes is otherwise invisible: jobs simply stop
making progress, no callback is sent, nothing looks broken, and no error is logged after
the first refusal. `"remaining": 0` explains it in one line. See
[03-configuration.md](03-configuration.md#fleet-wide-provider-budget).

Read-only. The API never spends the budget; only workers make paid calls.

---

## Error envelope

Every non-2xx response uses one shape.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "pageNumber is required for PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
    "details": { "fields": [{ "loc": ["body"], "msg": "..." }] },
    "requestId": "01J8Z3K7QF2E9V0S6M4B1TX7YD"
  }
}
```

`message` is always safe to log and display. `details` never carries a token, a presigned
URL or a stack trace — traces go to our logs, keyed by `requestId`.

| Code | HTTP | Retryable |
|---|---:|:---:|
| `VALIDATION_ERROR` | 422 | no |
| `AUTHENTICATION_ERROR` | 401 | no (inbound) |
| `AUTHORIZATION_ERROR` | 403 | no |
| `NOT_FOUND` | 404 | no |
| `CONFLICT` | 409 | no |
| `RATE_LIMITED` | 429 | yes |
| `SOURCE_UNAVAILABLE` | 503 | yes |
| `SOURCE_INVALID` | 422 | no |
| `AI_INFERENCE_ERROR` | 500 | yes |
| `MODEL_UNAVAILABLE` | 503 | yes |
| `PROCESSING_TIMEOUT` | 504 | yes |
| `UPLOAD_FAILED` | 502 | yes |
| `CALLBACK_FAILED` | 502 | yes |
| `EXTERNAL_SERVICE_ERROR` | 503 | yes |
| `INTERNAL_ERROR` | 500 | yes |

---

## Authentication

**Validation is implemented and tested. `AUTH_ENFORCE` defaults to `false` only because
`Q-01` is unanswered.**

The V1 spec states that inbound calls carry `Authorization: Bearer
{ai-engine-bearer-token}` but never says how to validate it — no algorithm, no JWKS
location, no expected audience, and no indication of which Keycloak client the Backend
presents.

So the mechanism is built: `adapters/auth/jwks_verifier.py` performs RS256-only JWKS
verification with `iss`/`exp`/`nbf` checks, an `azp`/`aud` allowlist, rotation-aware key
refresh, and fail-closed behaviour on every error path. It is covered by 35 unit tests
against real RSA keys and 17 API tests. **Turning it on is two environment variables:**

```bash
AUTH_ENFORCE=true
BACKEND_ALLOWED_CLIENT_IDS=reading-first-backend   # <- the value we need from you
```

What we do not know is that value, and whether `aud` should be required. Until then:

- accepts requests without a verified token,
- logs `auth.not_enforced` as a **warning on every boot**,
- logs `auth.absent` or `auth.observed` per request so a staging call reveals the real
  client id,
- reports it in `/ready` as `"not enforced (blocked on Q-01)"`.

When `AUTH_ENFORCE=true`, `BACKEND_ALLOWED_CLIENT_IDS` becomes **required** — startup fails
without it, because enforcing auth with an empty allowlist would accept every valid token.
And if enforcement is on with no verifier wired, every request is rejected rather than
admitted: a verifier that fails open is worse than none, because it looks like protection.

`aud` is **not** required by default. Keycloak `client_credentials` tokens do not reliably
carry a predictable audience, so requiring one would reject valid tokens; the caller's
identity comes from `azp` instead, falling back to `aud`. Set `JWT_AUDIENCE` if you do issue
one.

Symmetric algorithms are rejected at startup: accepting HS256 next to a JWKS verifier is
the classic algorithm-confusion vulnerability, where the public key becomes a valid HMAC
secret. Asserted by `test_symmetric_jwt_algorithms_are_rejected`.

> The engine's own `client_secret` is an **outbound** credential for calling the Backend. It
> must never be used as an inbound shared secret — that would let either service
> impersonate the other and require synchronised rotation.

## Rate limiting

**Inbound: none.** There is one trusted first-party caller. A limiter would most likely
fire during the Backend's retry storm — exactly when rejecting work is least helpful.

**Outbound: yes,** and it is the one that matters. Paid provider calls are metered against
a fleet-wide budget (`PROVIDER_CALL_BUDGETS`), because the free tiers this system runs on
are small enough that a single book can exhaust a day's allowance. A `429` from a provider
is handled by the shared retry policy, which honours `Retry-After` in both RFC 7231 forms.
