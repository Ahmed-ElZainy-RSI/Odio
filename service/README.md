# Reading First AI Engine

The service that lets the Backend's book APIs drive this repository's existing Arabic OCR
and TTS pipelines over HTTP, per `assets/Backend & AI integration V1.pdf`.

**Design documents:** [`../docs/backend-v1/`](../docs/backend-v1/) — the architecture,
requirements extraction and decision records this code implements.
**Implementation documents:** [`docs/`](docs/) — how *this* code is wired, run and extended.

> This is a standalone package. It does not touch, import, or depend on `walidapro/api/`.

## Status: integration-ready

**Both job types run end to end.** A real PDF goes in and the mandated protocol comes out;
a page's free text goes in and audio plus a timing JSON come out.

```
616 tests · 40s · zero network calls · zero paid API calls
8,738 lines production (65 files) · 8,464 lines tests (35 files)
```

| Capability | State |
|---|---|
| `POST /api/jobs` — idempotent accept, 202, 409 on payload conflict | ✅ |
| **Inbound Keycloak JWT validation** (JWKS, RS256, `azp` allowlist, 401 vs 403) | ✅ |
| Keycloak `client_credentials` token provider, single-flight, 30 s margin | ✅ |
| Source fetch, 403-as-expiry recovery, SSRF controls, magic-byte checks | ✅ |
| Three-step upload cycle, ordered, with ticket refresh | ✅ |
| All four progress callbacks, via a durable outbox | ✅ |
| **Job A** — render, extract, diacritize-ready, both artifacts, per-page reports | ✅ |
| **Job B** — TTS, forced alignment, timing JSON, both artifacts | ✅ |
| Page ledger, artifact ledger, leases, reconciler | ✅ |
| **Worker loop** with graceful shutdown and per-page deadlines | ✅ |
| One shared retry policy: full jitter, `Retry-After`, 401-refresh-once, budget cap | ✅ |
| **Durable job store** — SQLite in Postgres shape: WAL, `BEGIN IMMEDIATE` claims, partial unique indexes, restart-safe | ✅ |
| **All three OCR engines** — PDF text layer (free, exact), Gemini Vision, LightOn — selected per document | ✅ |
| Gemini TTS adapter (paid) + synthetic TTS (free, for tests and dev) | ✅ |
| **Fleet-wide provider budget** — shared across workers; exhaustion **parks** work instead of failing it | ✅ |
| **Container** — one image, three roles, non-root, plus Compose | ✅ `T-7.*` |
| **CI** — ruff, both test suites, an image build, and a container smoke test | ✅ `T-6.6` |
| Postgres `JobStore` for multi-host deployment | ⬜ `T-4.1` |

Deployable as-is on a single host: SQLite takes one writer, which is enough for one API
plus N workers sharing a volume. Two hosts need `T-4.1` — and because the SQL is already
written in Postgres shape, that is a translation rather than a redesign
(`POSTGRES_DELTA` in `adapters/persistence/sql.py` lists the four differences).

## Verified against the real corpus

The text-layer OCR adapter is not a stub. On `assets/PDF Samples/النجوم.pdf` it reproduces
the legacy pipeline's output exactly — the same 44 tokens on page 3, the same `diac_ratio`
of 0.791, and the same first token:

```
Token(id='p003w000', text='الأَبُ', raw='ِالَْبُ',
      box=(8.096, 61.919, 84.865, 5.892), fixes=('leading-punct', 'dropped-hamza'))
```

Byte-identical to `work/النجوم/p003.json` on disk. Because that path reads the PDF's
embedded text layer, Job A on a born-digital book costs **zero paid API calls** — and 25 of
the 36 sample books qualify.

## Test coverage

| Suite | Tests | What it proves |
|---|---:|---|
| `tests/unit/` | 429 | Pure logic, exhaustively. Retry decision table (66), store contracts run against **both** stores (46), budgets (59), JWKS auth against real RSA keys (35), OCR providers (34), error taxonomy and redaction (27), property-based invariants (23), worker loop (23) |
| `tests/api/` | 59 | HTTP contract, error envelope, idempotency races, SSRF rejection, enforced auth (17), `/ops/budget` |
| `tests/behaviour/` | 25 | The V1 protocol as spec scenarios, each quoting the sentence it enforces |
| `tests/e2e/` | 103 | Both jobs end to end against a contract fake, a **42-case fault-injection matrix**, crash-resume durability, and a provider quota wall |

Highlights worth knowing about:

- **`tests/e2e/test_fault_matrix.py`** asserts one property across 16 injected faults × both
  job types: *every expected page has a terminal outcome that is either delivered or
  durably queued, and never reported twice.* That is INVARIANT-1, stated so it stays true
  even when the Backend itself is unreachable.
- **`tests/unit/test_properties.py`** uses hypothesis over arbitrary Arabic text,
  whitespace, page numbers and timing sequences — including the Job A → Job B id round trip.
- **`test_job_a_text_artifact_feeds_job_b_with_identical_ids`** runs Job A on a real book,
  takes the text artifact that actually reached storage, feeds it to Job B, and asserts the
  timing JSON's ids match. Nothing else verifies the two jobs agree.
- **`tests/e2e/test_durability.py`** kills a worker mid-book and hands the job to another
  one. This is where the worst bug found so far surfaced: a page could be reported `failed`
  and then `completed` — two contradictory reports for one page. Fixed in three layers.
- **`tests/e2e/test_provider_budget.py`** runs a book into a quota wall. The pages that
  never started are **parked, not failed**: no callback, ledger row still `PENDING`, job
  deferred until the window resets — then it finishes, with every page reported once.

## Quick start

```bash
cd service
python -m pip install -e '.[dev]'

python -m pytest -q                    # 616 passed, ~40s
python -m pytest -q -m "not slow"      # 607 passed, ~19s — the fast loop
python -m ruff check . && python -m ruff format --check .

AI_ENGINE_JSON_LOGS=false \
SOURCE_URL_ALLOWED_HOSTS=storage.example.com \
python -m uvicorn --factory ai_engine.api.app:create_app --port 9099
```

Or bring up the whole thing locally — API + worker + a fake Backend, three real processes
over real sockets, no credentials and no paid calls:

```bash
./devtools/local_stack.sh          # then POST a job; it prints the curl
```

A real 16-page Arabic book completes in ~3s: 16/16 pages reported, 32 artifacts confirmed,
one cached token for ~117 calls. `POST /_faults` on the fake injects upload/callback/
presigned-expiry failures so you can watch the recovery paths by hand. Details in
[docs/04-local-development.md](docs/04-local-development.md#run-the-whole-thing-locally).

Or in containers — one image, three roles:

```bash
cd ..                                  # build context is the repository root
docker build -f service/Dockerfile -t ai-engine:dev .
docker compose -f service/docker-compose.yml up
```

See [docs/08-operations.md](docs/08-operations.md#running-it) for what to scale.

## The invariant this service exists to satisfy

> **INVARIANT-1.** For a job with `pagesCount = N`, deliver exactly one terminal callback —
> `/completed` or `/failed` — for every page in `1..N`, or fail the whole job.

The V1 spec: *"the backend will consider that you finish your process after you completed
the 30 pages whether they fail or succeed, if 29, no, the backend will wait for number 30
page."* A dropped page callback stalls a book in the Backend **forever**.

Every page loop in the legacy pipelines violates this — they `break` on the first error
([`run_ocr_gemini.py:59-62`](../src/pipeline/run_ocr_gemini.py#L59-L62)). Five mechanisms
here exist to satisfy it:

| Mechanism | Covers |
|---|---|
| Page ledger created before any page work | a page forgotten by omission |
| Callback outbox, written with the state change | death between upload and report |
| One **terminal** outbox slot per page, in the domain and in SQL | a double report — or worse, a `failed` *and* a `completed` for one page |
| Leases on job and page claims | a worker that stops without failing |
| Reconciler as deadline enforcer | "retried forever" → "reported failed once" |

## Layout

```
ai_engine/
├── domain/        stdlib ONLY — entities, enums, errors, INVARIANT-1 predicates
├── ports/         Protocols. Specifications, no implementations.
├── application/   accept · run job A · run job B · run page · outbox · reconcile · loop
├── artifacts/     the deterministic tokenizer + the two missing artifact projections
├── adapters/      the only layer allowed to import SDKs or the legacy pipelines
│   ├── ai/        PDF renderer · three OCR engines · TTS · aligner · legacy bridge
│   ├── auth/      JWKS inbound token verifier
│   ├── backend/   Keycloak tokens · files API · jobs API
│   ├── http/      the one shared retry policy
│   ├── storage/   presigned source fetch + SSRF controls
│   └── persistence/  durable SQLite JobStore + in-memory · fleet-wide provider budget
├── api/           FastAPI: routers, schemas, auth, error envelope
├── observability/ structured logging with redaction, metrics, correlation
├── config/        pydantic-settings; NO load_dotenv, anywhere
├── worker.py      composition root
└── main.py        one entrypoint per role

devtools/           local_stack.sh + the contract fake served over a real socket
Dockerfile          multi-stage; one image, three roles, non-root
docker-compose.yml  api + worker + reconciler on one shared volume
.env.example        a template for env injection — the service never reads a .env
```

CI lives at [`../.github/workflows/service-ci.yml`](../.github/workflows/service-ci.yml)
and gates ruff, this suite, the 56 pre-existing `src/tests`, the image build, and a
container smoke test that proves a job accepted by the API is claimable by a worker.

Three design claims are **enforced by tests**, not convention: `domain/` imports only the
standard library, `api/` cannot import an adapter or the legacy pipelines, and `load_dotenv`
appears nowhere (AST-checked). See [docs/01-architecture.md](docs/01-architecture.md).

## Two open questions from the Backend team

Neither blocks development any more — both have working implementations behind a flag — but
both need answering before go-live:

- **`Q-01` — what should we expect in the inbound token?** JWKS validation is implemented
  and tested; `AUTH_ENFORCE` defaults to `false` because we do not know which Keycloak
  client you present or what `aud`/`azp` to require. Set `AUTH_ENFORCE=true` and
  `BACKEND_ALLOWED_CLIENT_IDS` and it enforces immediately.
- **`Q-04` — is the `Text` artifact raw OCR text or final corrected + diacritized text?**
  We build the latter, because it is what the reader displays and what Job B feeds to TTS.

Full register: [`../docs/backend-v1/22-open-questions.md`](../docs/backend-v1/22-open-questions.md).
