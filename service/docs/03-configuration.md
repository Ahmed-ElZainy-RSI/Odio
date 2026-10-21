# Configuration

## Rules

1. **Environment only.** No config files, no database-stored settings, no runtime mutation.
2. **`load_dotenv()` appears nowhere in this package** — AST-enforced by
   `test_no_load_dotenv_anywhere_in_the_service_package`. And the service **refuses to
   start** if a `.env` exists on disk. See [Why](#why-a-env-on-disk-is-fatal).
3. **Fail fast.** A missing required secret or a malformed URL aborts startup naming the
   key, rather than surfacing as a 500 on the first job.
4. **Injected, not global.** Settings are constructed once at the process edge and passed
   by dependency injection, so a worker can be reconfigured by restart and tests can build
   variants freely.
5. **Secrets never printed.** `SecretStr` fields are redacted in `repr()` — asserted by
   `test_secret_is_not_exposed_in_repr`.

## List values

`SOURCE_URL_ALLOWED_HOSTS`, `BACKEND_ALLOWED_CLIENT_IDS`, `JWT_ALGORITHMS` and
`PROVIDER_CALL_BUDGETS` accept **either** form:

```bash
SOURCE_URL_ALLOWED_HOSTS='storage.internal, s3.eu-west-1.amazonaws.com'   # CSV
SOURCE_URL_ALLOWED_HOSTS='["storage.internal","s3.eu-west-1.amazonaws.com"]'  # JSON
```

This needed explicit handling: `pydantic-settings` JSON-decodes env values for complex
types *before* field validators run, so the plain CSV form raised a parse error and crashed
the process at boot. The fields are annotated `NoDecode` and the validator accepts both.
The unit tests missed it because they inject Python lists via the constructor — which is
why `tests/unit/test_config.py` now has a section that goes through `os.environ`.

## Settings

### Role and server

| Key | Type | Default | Notes |
|---|---|---|---|
| `AI_ENGINE_ROLE` | `api`\|`worker`\|`reconciler` | `api` | Selects the entrypoint |
| `AI_ENGINE_PORT` | int | `9099` | Matches the spec's example |
| `AI_ENGINE_LOG_LEVEL` | str | `INFO` | |
| `AI_ENGINE_JSON_LOGS` | bool | `true` | `false` gives human-readable console output |
| `AI_ENGINE_VERSION` | str | `0.1.0` | Stamped on every log line |

### Backend integration — REQ-CFG-01

| Key | Required | Default | Notes |
|---|:---:|---|---|
| `BACKEND_BASE_URL` | ✔ | `https://localhost:6060` | Must be absolute http(s) |
| `TOKEN_URL` | ✔ | Keycloak realm token endpoint | Must be absolute http(s) |
| `AI_ENGINE_CLIENT_ID` | ✔ | `reading-first-ocr-engine` | |
| `AI_ENGINE_CLIENT_SECRET` | ✔ (worker) | — | **Highest-value secret.** The API role does not need it. |

### Inbound token validation — blocked on `Q-01`

| Key | Default | Notes |
|---|---|---|
| `AUTH_ENFORCE` | `false` | See [02-api-reference.md](02-api-reference.md#authentication) |
| `JWKS_URL` | derived | `TOKEN_URL` with `/token` → `/certs` |
| `BACKEND_ALLOWED_CLIENT_IDS` | empty | **Required** when `AUTH_ENFORCE=true`; startup fails otherwise |
| `JWT_ALGORITHMS` | `RS256` | Asymmetric only. `HS256` is rejected at startup — accepting it next to a JWKS verifier is the classic algorithm-confusion vulnerability. |
| `JWT_CLOCK_LEEWAY_SECONDS` | `30` | Two servers are never perfectly in sync |
| `JWT_ISSUER` | derived from `TOKEN_URL` | The Keycloak realm URL |
| `JWT_AUDIENCE` | unset | Optional. Not required by default, because `client_credentials` tokens do not reliably carry `aud`. |

### Security — SSRF control

| Key | Default | Notes |
|---|---|---|
| `SOURCE_URL_ALLOWED_HOSTS` | empty | **Set this before exposing the service.** Empty logs a warning and does not host-restrict. |
| `SOURCE_URL_REQUIRE_HTTPS` | `true` | |
| `SOURCE_URL_RESOLVE_AND_CHECK` | `true` | Resolve the host and reject private/loopback/link-local addresses. **Keep it on.** |

`sourceDownloadUrl` is caller-supplied and fetched **server-side**, so an unrestricted
engine is an open proxy into whatever network the worker sits in — cloud metadata
endpoints, internal admin panels, a database on localhost. Three controls apply: a scheme
allowlist, a host allowlist, and resolve-then-reject for loopback/link-local/private
addresses. Loopback and metadata hostnames are blocked unconditionally, even if someone
adds them to the allowlist by mistake.

The three do not all run in the same place, and that matters. The API applies the scheme
check, the literal-host blocklist and the allowlist at **accept** time. The worker applies
all of them again at **fetch** time, and the resolve-then-reject check runs only there —
which is the point: the API compares strings, and only the worker, which is about to open
the socket, can ask what the name actually resolves to. An allowlisted hostname whose DNS
record points at `169.254.169.254` is caught by that check and by nothing else.

`SOURCE_URL_RESOLVE_AND_CHECK=false` turns it off, and exists for exactly one reason:
pointing a worker at a fake Backend on loopback (`devtools/local_stack.sh`). The API logs
`source_url.ssrf_resolve_check_disabled` at every boot when it is off. Do not ship with it
off — and note that turning it off is *not* required to run locally against
`fake.localhost`, only to fetch from a private address.

### Job store — durability

| Key | Default | Notes |
|---|---|---|
| `JOB_STORE` | `sqlite` | `sqlite` \| `memory`. Anything else fails at startup. |
| `JOB_STORE_PATH` | `/var/lib/ai-engine/jobs.db` | The job ledger, the callback outbox **and** the provider budget |

**Every role must point at the same path.** The API writes the job, the worker claims it,
the reconciler enforces its deadline; they coordinate only through this file. An API
serving one store while a worker polls another is the failure mode where every job returns
`202` and then nothing ever happens — which is exactly what
`test_the_api_serves_the_configured_store_when_none_is_injected` exists to prevent.

`memory` is per-process and loses every in-flight job on exit. Selecting it logs a warning
rather than being silently acceptable.

SQLite takes **one writer**, which is enough for one API plus N workers on one host. Two
hosts need Postgres (`T-4.1`); `POSTGRES_DELTA` in
`ai_engine/adapters/persistence/sql.py` records the four things that change.

### Storage

| Key | Default | Notes |
|---|---|---|
| `SCRATCH_DIR` | `/var/tmp/ai-engine` | One ephemeral directory per job, named by the job UUID |
| `MODEL_CACHE_DIR` | `/var/cache/ai-engine/models` | Must be persistent once the alignment model is wired |
| `MAX_SOURCE_BYTES` | `209715200` (200 MB) | `Q-08` |
| `MAX_SOURCE_PAGES` | `500` | Bounds the ledger fan-out |

Scratch directories are named by **job UUID**, never by the PDF filename. The legacy
pipeline uses `work/{pdf_path.stem}`, and the real directory names in the corpus contain
Arabic text, spaces, `?` and `' - Copy'` — unusable as an identity and unsafe as a path.

### Concurrency, deadlines, retries

| Key | Default | Notes |
|---|---|---|
| `WORKER_JOB_CONCURRENCY` | `2` | |
| `WORKER_PAGE_CONCURRENCY` | `4` | Bounds CPU and memory per worker |
| `JOB_PAGE_CONCURRENCY` | `4` | Stops one long book monopolising the fleet |
| `WORKER_LEASE_SECONDS` | `300` | An expired lease means the worker is presumed dead |
| `PAGE_TASK_TIMEOUT_SECONDS` | `900` | Turns a hang into `PROCESSING_TIMEOUT` |
| `JOB_DEADLINE_SECONDS` | `86400` | What the reconciler enforces |
| `PAGE_MAX_ATTEMPTS` | `3` | |
| `JOB_MAX_ATTEMPTS` | `3` | |
| `CALLBACK_MAX_ATTEMPTS` | `10` | Then the record is parked and alerted on |
| `RECONCILE_INTERVAL_SECONDS` | `60` | |
| `RETRY_MAX_ATTEMPTS` | `5` | Attempts per outbound call |
| `RETRY_BASE_DELAY_SECONDS` | `0.5` | Exponential base, with full jitter |
| `RETRY_MAX_DELAY_SECONDS` | `60` | Caps both our backoff and a hostile `Retry-After` |
| `RETRY_TOTAL_BUDGET_SECONDS` | `120` | Wall-clock ceiling across all attempts. An attempt cap alone lets a slow endpoint hold a worker past any useful deadline. |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | `5` | |
| `HTTP_READ_TIMEOUT_SECONDS` | `60` | |

### Feature flags

| Key | Default | Notes |
|---|---|---|
| `DEFAULT_QUALITY_TIER` | `fast` | |
| `ENABLE_VERIFIED_TIER` | `false` | **Hard kill-switch.** A job requesting `verified` is *downgraded and logged*, not rejected. |
| `OCR_ENGINE` | `auto` | `auto` \| `textlayer` \| `gemini` \| `lighton`. Validated at startup. See [OCR engine selection](#ocr-engine-selection). |
| `DIACRITIZER` | `none` | `none` \| `local`. Job A diacritisation (spec 016). `local` runs the CPU `arabic-diacritizer` model on pages below `DIAC_THRESHOLD`; the worker **refuses to start** if the model can't load. Pages printed with tashkeel are kept; a page the model would damage (word count or letters changed) ships undiacritised. Metric: `ai_engine_diacritization_total{outcome=applied\|kept\|review\|rejected}`. |
| `TTS_ENGINE` | `gemini` | `gemini` (paid) or `synthetic` (free, real WAV, for dev) |
| `TTS_NARRATOR` | `female` | V1 uploads exactly one `Audio` artifact |
| `ALIGNMENT_ENGINE` | `whisper` | `whisper` (forced) or `native` (Hume timestamps) |

The verified-tier switch defaults off because that tier is **65–200 paid calls per page**
against the fast tier's 1–2 — dominated by one vision call per Arabic word. Downgrading
rather than rejecting is deliberate: rejecting would fail work the Backend cannot fix,
while silently honouring the request risks a 40–100× cost surprise.

### Model configuration

Model ids are settings, not constants. In the legacy tree they are hardcoded in seven
places and several are `-preview` models, so a deprecation would become a code change
during an incident. Here a rollback is a config change plus a restart.

| Key | Default |
|---|---|
| `OCR_GEMINI_MODEL` | `gemini-3.6-flash` |
| `TTS_GEMINI_MODEL` | `gemini-3.1-flash-tts-preview` |
| `TTS_GEMINI_VOICE` | `Kore` |
| `ALIGN_WHISPER_MODEL_SIZE` | `small` |
| `ALIGN_WHISPER_DEVICE` | `cpu` |
| `ALIGN_WHISPER_COMPUTE_TYPE` | `int8` |
| `DIAC_THRESHOLD` | `0.15` |
| `GEMINI_API_KEY` | — | Secret. Required for the paid TTS path. |

**No GPU is required anywhere in V1.** Every local model in the legacy tree is CPU-pinned
by hardcoded choice; the expensive models are remote.

### Provider credentials

| Key | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | — | Secret. Needed for `OCR_ENGINE=gemini` and `TTS_ENGINE=gemini`. |
| `LIGHTON_API_KEY` | — | Secret. Needed for `OCR_ENGINE=lighton`. |
| `LIGHTON_BASE_URL` | `https://api.lighton.ai` | |

A missing key is **not** a startup failure, because which key a deployment needs depends on
`OCR_ENGINE` and on the documents themselves. It is refused at *engine selection*: the job
fails once with `MODEL_UNAVAILABLE` before a single page is rendered, rather than failing
every page after paying to render each one.

### OCR engine selection

`auto` is resolved **per document**, after the source PDF is on disk and before page
fan-out, because the answer depends on the document:

| `OCR_ENGINE` | Behaviour |
|---|---|
| `auto` | The embedded text layer if the PDF has one (exact, free, and 25 of the 36 sample books qualify), otherwise Gemini Vision |
| `textlayer` | Always the text layer. Fails per page on a scanned book. |
| `gemini` | Always Gemini Vision. Per-line boxes, subdivided into word boxes by the legacy tokenizer, so `box_level` is `"line"`. |
| `lighton` | Always LightOn. Markdown only — the `box` field is **omitted** rather than fabricated, and `box_level` is `"none"`. |

The engine is resolved once per document and cached, not once per page: `auto` reads the
PDF to decide, and a 200-page book should not answer that question 200 times.

### Fleet-wide provider budget

| Key | Default | Notes |
|---|---|---|
| `PROVIDER_CALL_BUDGETS` | empty (unmetered) | `provider=calls/seconds` specs, e.g. `gemini=10/86400,hume=500/86400` |

Empty by default **on purpose**: a wrong low value silently parks real books, which is a
worse failure than an unmetered one. But it should be set. `README.md:491` in the parent
repo records a real free-tier ceiling of **10 requests/day per model**, and a Hume quota
that ran out mid-book left `work/المومياوات ناشيونال جيوجرافيك` with 33 page records and
zero audio. A book averages 31.4 pages, so **one book can exhaust one day's allowance**.

* The counter lives in `JOB_STORE_PATH`, charged under `BEGIN IMMEDIATE`, so it is shared
  across worker processes. Scaling workers does not scale the paid call rate.
* Windows are fixed and epoch-aligned, not sliding — the only kind two processes can agree
  on from a timestamp with no coordination. The cost is a possible burst across a boundary.
* On exhaustion the page is **parked**: no callback is sent, its ledger row stays `PENDING`,
  and the job's lease is held until the window resets. Parking is not failing — nothing was
  attempted, so reporting `failed` would be a lie the Backend would act on.
* `provider=0/86400` is a per-provider kill switch, and is legal rather than an error.
* `GET /ops/budget` reports what is left. Alert on
  `ai_engine_provider_budget_exhausted_total`; the reconciler's deadline is the backstop if
  an allowance never returns.
* With `JOB_STORE=memory` the budget can only be per-process, and says so in a warning.

### Legacy pipeline

| Key | Default | Notes |
|---|---|---|
| `LEGACY_REPO_ROOT` | unset | Path to the directory containing `src/`. **Required for every OCR engine and for alignment.** `/app` in the shipped image; the repository root locally. |

`src/pipeline` is not an installed distribution — it has no packaging and resolves only via
`sys.path`. `adapters/ai/legacy.py` is the single module that knows this, which is what
lets the import-rule test keep asserting that `api/` cannot reach it.

## Why a `.env` on disk is fatal

Two facts in the legacy tree combine badly:

1. `load_dotenv()` runs **at module import** in 11 modules.
2. A real `.env` with eight live credentials sits at the repository root.

Together: if a `.env` is present in the container's working directory, importing the
pipeline **silently overrides orchestrator-injected secrets**. A key rotated in the
environment would be shadowed by a stale key in a file, with no log line and no error.

`assert_no_dotenv_on_disk()` runs before the API starts and refuses to continue, converting
that silent override into a loud failure. `.env.example` is a template for your
orchestrator's injection, not a file to copy into place.
