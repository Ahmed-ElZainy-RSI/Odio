# Status and roadmap

Task ids are from
[`../../docs/backend-v1/21-implementation-roadmap.md`](../../docs/backend-v1/21-implementation-roadmap.md).

```
616 tests · 40s · zero network calls · zero paid API calls
8,738 lines production (65 files) · 8,464 lines tests (35 files)
607 of the 616 run in 19s; the 9 marked `slow` render a real 16-page book
```

Verified on the last run: `ruff check .` and `ruff format --check .` clean, `pytest -q`
616 passed, the pre-existing `src/tests` suite still 56 passed, and the container image
builds and serves `/health`, `/ready` and `/ops/budget`.

## Done

| Task | What landed |
|---|---|
| `T-1.1` | `pyproject.toml`, three console entrypoints, dependency groups (`sql`, `metrics`, `ai`, `dev`) |
| `T-1.2` | `pydantic-settings` config, no `load_dotenv`, refuses to start if a `.env` is on disk |
| `T-1.3` | `domain/` (stdlib-only, AST-enforced) and 12 port Protocols with written contracts |
| `T-1.4` | 15-code error taxonomy, HTTP mapping, retryable classification, `FailureReason` redacting at construction |
| `T-1.6` | Structured logging with two-layer redaction, metric registry, correlation contextvars |
| `T-2.1` | FastAPI app, `/health` (no dependency checks), `/ready` (per-dependency), `/metrics`, `/ops/budget` |
| **`T-2.2`** | **Inbound JWKS JWT validation** — RS256 only, `iss`/`exp`/`nbf`, `azp` allowlist, refresh that survives key rotation, fails closed |
| `T-2.3` | Request DTOs, discriminated union, path-traversal and SSRF validation |
| `T-2.4` | Idempotent accept — atomic insert-if-absent, `409` on a differing payload, `503` when the store is unreachable |
| **`T-2.5`** | **Worker loop** — claim, run, drain, reconcile on a timer, graceful `SIGTERM`, survives store/drain/runner failures |
| `T-2.6` | `GET /api/jobs/{jobId}` for INVARIANT-1 debugging |
| **`T-4.1`** | **Durable job store** — SQLite in Postgres shape: WAL, `BEGIN IMMEDIATE` claims, partial unique indexes, versioned schema. Restart-safe and multi-process. `POSTGRES_DELTA` in the module records exactly what changes for Postgres. |
| `T-3.2` | `page_count()` as a real return value, with a `MAX_SOURCE_PAGES` ceiling |
| **`T-3.3`** | **All three OCR engines** — the free PDF text-layer path, Gemini Vision (per-line boxes subdivided by the legacy tokenizer), LightOn (markdown, no fabricated boxes). `OCR_ENGINE=auto` resolves per document and prefers the free path. |
| `T-3.4` | Deterministic tokenizer and the `Text` artifact builder |
| **`T-3.5`** | **Job B** — Gemini TTS adapter, synthetic TTS for tests, forced-alignment aligner reusing `speak.bind` verbatim, `RunAudioJob` |
| `T-3.7` | The timing `JSON` artifact builder, with validation and `anchor_ratio` |
| `T-4.2` | Page ledger, created before any page work, with leases. Single-page form for Job B. |
| `T-4.3` | Artifact ledger — a row only after `confirm-upload`, so a retry skips what landed |
| `T-4.4` | Callback outbox. Uniqueness **collapses `page_completed` and `page_failed` into one terminal slot per page**, in the domain and again as a partial unique index |
| `T-4.5` | Reconciler as deadline enforcer, correct for both job types |
| `T-5.1` | Keycloak token provider — in-memory, single-flight, 30 s margin, never retries its own 401 |
| `T-5.2` | The one shared retry policy: pure decision table, full jitter, `Retry-After` in both RFC forms, 401-refresh-once, wall-clock budget |
| `T-5.3` | Presigned source fetch, 403-as-expiry recovery, magic-byte checks, SSRF controls |
| `T-5.4` | Metadata callback **as a real gate** on page work |
| `T-5.5` | The three-step upload cycle, ordered, with ticket refresh |
| `T-5.6` | All four progress callbacks via the outbox |
| **`T-5.7`** | **Fleet-wide provider budget** — a counter per provider per fixed window in the job store's own database, charged under `BEGIN IMMEDIATE`. On exhaustion a page is **parked, not failed**: no callback, ledger row stays `PENDING`, job lease held until the window resets. `GET /ops/budget` shows what is left. |
| `T-6.1` | **The contract fake** — Keycloak + storage + all five Backend endpoints, 16 injectable faults, call-order recording, and it enforces the contract back at us |
| `T-6.2` | The INVARIANT-1 suite, including a 42-case fault matrix and crash-resume durability tests |
| `T-6.3` | Auth and token suites — 35 unit + 17 API tests against real RSA keys |
| `T-6.5` | Artifact contract tests, including byte stability and the Job A → Job B round trip |
| **`T-6.6`** | **CI** — [`../../.github/workflows/service-ci.yml`](../../.github/workflows/service-ci.yml): ruff, `ruff format --check`, both test suites, an image build, and a container smoke test that proves a job accepted by the API is claimable by a worker |
| **dev tooling** | The contract fake served over a real socket ([`../devtools/fake_backend_server.py`](../devtools/fake_backend_server.py)) plus `local_stack.sh` — API, worker and fake Backend as three real processes, with runtime fault injection over HTTP. Verified: a real 16-page book completes in ~3s, and completes again through injected upload, confirm, callback and presigned-expiry failures. |
| **`T-7.1`–`T-7.3`** | **Container** — multi-stage [`../Dockerfile`](../Dockerfile) (one image, three roles, non-root), [`../docker-compose.yml`](../docker-compose.yml), a root `.dockerignore` that keeps 895 MB of samples out of the build context, and an opt-in build arg that bakes the 464 MB of whisper weights in |

## Next, in dependency order

### `T-4.1` (Postgres proper) · M

The durable store is done and deployable as SQLite: one host, one writer, restart-safe.
Postgres is what makes it *many hosts*. Nothing above the port changes —
`tests/unit/test_job_store.py` (46 tests) is written against the port's documented
behaviour and is parametrized over implementations, so a Postgres adapter joins the
`STORES` list and the same tests must pass unchanged.
`ai_engine/adapters/persistence/sql.py::POSTGRES_DELTA` records the four differences.
See [06-extending.md](06-extending.md#worked-example-the-postgres-store-t-41).

### `T-8.1` / `T-8.2` — preconditions for the verified tier · L

Parallelize the per-word diacritics loop, and give `walidapro/` a test suite. Both are
required before `ENABLE_VERIFIED_TIER` should ever be flipped on.

### `T-6.4` (part) — golden fixtures from the real corpus · S

`work/فلفل المغرور-lighton/p003.json` and `work/النجوم/p003.json` cover both the
box-bearing and box-free variants. The text-layer adapter is already asserted
byte-identical against the second; the LightOn adapter's output shape is asserted from a
transport stub rather than from that recorded artifact.

## Deliberately not done

| Not built | Why |
|---|---|
| The verified tier (`walidapro/ocr_verifier`) | 65–200 paid calls per page against the fast tier's 1–2, and zero tests across 16,155 lines. The `PageVerifier` port exists so enabling it later is configuration, not architecture. |
| The human-in-the-loop correction path | `tools/context_detector.py` seeds every decision as `"reject"` and the chain literally stops and says so. Automating it would defeat its purpose. |
| Bounding boxes as an artifact | The V1 contract has **no `artifactType` slot for them at all**. That is a contract gap (`Q-10`), not an implementation task. |
| A message broker | The store handles the queue at book-scale volumes, and a durable store is required anyway. |
| Inbound rate limiting | One trusted caller, and a limiter would fire during their retry storm. |
| GPU inference | Every local model is CPU-pinned by hardcoded choice. |
| Anything from `walidapro/api/` | That is a teammate's parallel implementation. This package does not read, import or merge it. |

## Known gaps in what *is* built

Stated plainly rather than discovered later.

| Gap | Impact | Fix |
|---|---|---|
| The durable store is single-host | SQLite takes one writer. Fine for one API + N workers on one host; not fine for two hosts. | `T-4.1` (Postgres) |
| `AUTH_ENFORCE=false` by default | The service trusts its caller unless configured otherwise. Validation is implemented and tested; the default is off only because `Q-01` is unanswered. | answer `Q-01`, set two env vars |
| `PROVIDER_CALL_BUDGETS` empty by default | Nothing meters paid calls until it is set. Deliberate: a wrong low value silently parks real books. | set it per deployment |
| `SOURCE_URL_ALLOWED_HOSTS` empty by default | Logs a warning and does not host-restrict. **Set it before exposing the service.** | configuration |
| No redirect policy on the source fetch | The SSRF controls validate the URL we were given; a `302` to a private address is a separate question. `httpx` does not follow redirects by default, so this is currently safe by accident rather than by decision. | pin `follow_redirects=False` explicitly and test it |
| Parked work relies on the reconciler as its backstop | A provider allowance that never returns leaves pages parked until `JOB_DEADLINE_SECONDS`, then the job fails as a whole. That is the correct outcome, but it is a deadline, not a notification. | alert on `ai_engine_provider_budget_exhausted_total` |
| Timing quality is a model limit, not a bug | 21.8% of tokens interpolated across the corpus; 51% on the Gemini+whisper path versus 94% on Hume native. The artifact ships `anchor_ratio` so it is visible per page. | product decision (`T-8.7`) |
| The adapters `import fitz`, which PyMuPDF has deprecated | A future PyMuPDF release removes the alias. It warns on import today and works. | rename to `import pymupdf` in `renderer_pymupdf.py` and `ocr_textlayer.py` |
| No lock file | `pyproject.toml` pins floors, not versions, so two builds can differ. | `T-0.1` |
