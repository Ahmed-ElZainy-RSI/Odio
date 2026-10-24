# Configuration

## 1. Strategy

| Principle | Detail |
|---|---|
| **Environment only** | All configuration comes from the process environment. No config files, no database-stored settings, no runtime mutation. |
| **`pydantic-settings`, validated at startup** | One `Settings` model per concern, composed into an app settings object, constructed once at startup and passed by dependency injection — **not** imported as a module global. |
| **Fail fast** | A missing required secret or a malformed URL aborts startup with a clear message naming the key. It must never surface later as a 500 on the first job. |
| **No `load_dotenv()` in the service package** | Ever. See §3. |
| **Secrets never logged** | The settings object's `__repr__` redacts every `SecretStr` field. Redaction is a property of the type, not of the logging call site. |
| **Injected, not global** | The existing pattern — `walidapro/ocr_verifier/config.py:35`'s frozen `SETTINGS` singleton evaluated at import — cannot work in a worker: values are fixed at import, so a rotated credential needs a process restart and two jobs cannot use different thresholds. |

### Layers

| Layer | Contents |
|---|---|
| **Environment** | role, log level, service version, port |
| **Secrets** | `AI_ENGINE_CLIENT_SECRET`, DB password, all vendor API keys |
| **Model configuration** | every model id, voice, local model size, and threshold |
| **External services** | `BACKEND_BASE_URL`, `TOKEN_URL`, `JWKS_URL`, provider base URLs |
| **Database** | `DATABASE_URL`, pool sizes |
| **Storage** | scratch dir, model cache dir, size caps |
| **Feature flags** | `DEFAULT_QUALITY_TIER`, `ENABLE_VERIFIED_TIER`, `TTS_NARRATOR`, `ALIGNMENT_ENGINE` |
| **Runtime configuration** | concurrency, timeouts, retry counts, deadlines, provider budgets |

## 2. Settings catalogue

### 2.1 New for V1 — the service layer

| Key | Type | Required | Default | Layer | Consumer | Notes |
|---|---|:---:|---|---|---|---|
| `AI_ENGINE_ROLE` | enum | ✔ | — | env | both | `api` \| `worker` \| `reconciler`. Selects the entrypoint. |
| `AI_ENGINE_PORT` | int | | `9099` | env | api | Matches the PDF's `AI_Engine_BASE_URL` example |
| `AI_ENGINE_LOG_LEVEL` | enum | | `INFO` | env | both | |
| `AI_ENGINE_VERSION` | str | | build-injected | env | both | Stamped on every log line |
| `BACKEND_BASE_URL` | https URL | ✔ | — | external | worker | **REQ-CFG-01** — the PDF's `backend_Base_URL` |
| `TOKEN_URL` | URL | ✔ | — | external | worker | **REQ-CFG-01** — the PDF's `token_URL` |
| `AI_ENGINE_CLIENT_ID` | str | ✔ | — | external | worker | **REQ-CFG-01** — e.g. `reading-first-ocr-engine` |
| `AI_ENGINE_CLIENT_SECRET` | SecretStr | ✔ | — | **secret** | worker | **REQ-CFG-01.** Highest-value secret in the system. |
| `JWKS_URL` | URL | | derived from `TOKEN_URL` | external | api | Inbound token validation. Derived by replacing `/token` with `/certs`; overridable. |
| `BACKEND_ALLOWED_CLIENT_IDS` | csv | ✔ | — | external | api | The `azp`/`aud` allowlist. **Blocked on `Q-01`** — we do not yet know the value. |
| `JWT_ALGORITHMS` | csv | | `RS256` | external | api | RS256 only; HS256 must never be added ([12-security.md](12-security.md) §2) |
| `JWT_CLOCK_LEEWAY_SECONDS` | int | | `30` | external | api | |
| `DATABASE_URL` | DSN | ✔ | — | **secret** | both | |
| `DB_POOL_SIZE` | int | | `5` (api), `10` (worker) | database | both | |
| `SCRATCH_DIR` | path | | `/var/tmp/ai-engine` | storage | worker | |
| `MODEL_CACHE_DIR` | path | | `/var/cache/ai-engine/models` | storage | worker | Must be a persistent volume — 464 MB of weights |
| `SOURCE_URL_ALLOWED_HOSTS` | csv | ✔ | — | external | worker | **SSRF control. Required, not optional** ([12-security.md](12-security.md) §5) |
| `MAX_SOURCE_BYTES` | int | | `209715200` (200 MB) | storage | worker | `Q-08` |
| `MAX_SOURCE_PAGES` | int | | `500` | storage | worker | Bounds the ledger fan-out |
| `WORKER_JOB_CONCURRENCY` | int | | `2` | runtime | worker | |
| `WORKER_PAGE_CONCURRENCY` | int | | `4` | runtime | worker | |
| `JOB_PAGE_CONCURRENCY` | int | | `4` | runtime | worker | Stops one book monopolizing the fleet |
| `WORKER_LEASE_SECONDS` | int | | `300` | runtime | worker | |
| `PAGE_TASK_TIMEOUT_SECONDS` | int | | `900` | runtime | worker | The deadline that turns a hang into `PROCESSING_TIMEOUT` |
| `JOB_DEADLINE_SECONDS` | int | | `86400` | runtime | worker | |
| `PAGE_MAX_ATTEMPTS` | int | | `3` | runtime | worker | |
| `JOB_MAX_ATTEMPTS` | int | | `3` | runtime | worker | |
| `CALLBACK_MAX_ATTEMPTS` | int | | `10` | runtime | worker | |
| `RECONCILE_INTERVAL_SECONDS` | int | | `60` | runtime | reconciler | |
| `RETENTION_DAYS_TERMINAL_JOBS` | int | | `90` | runtime | reconciler | `Q-12` |
| `PROVIDER_RATE_BUDGET_GEMINI` | int/day | | conservative | runtime | worker | Real free-tier walls exist — `README.md:491` records 10 req/day per model |
| `PROVIDER_RATE_BUDGET_LIGHTON` | int/day | | conservative | runtime | worker | |
| `HTTP_CONNECT_TIMEOUT_SECONDS` | float | | `5` | runtime | worker | |
| `HTTP_READ_TIMEOUT_SECONDS` | float | | `60` | runtime | worker | Per-operation overrides in [14-error-model.md](14-error-model.md) §7 |

### 2.2 New for V1 — feature flags

| Key | Type | Default | Notes |
|---|---|---|---|
| `DEFAULT_QUALITY_TIER` | enum | `fast` | Applied when the job body omits `qualityTier` ([ADR-009](19-adrs.md)) |
| `ENABLE_VERIFIED_TIER` | bool | `false` | A hard kill-switch. Even a job asking for `verified` is downgraded (and logged) when this is false — protects against a 40–100× cost surprise. |
| `VERIFIER` | `visual` \| `passthrough` | `visual` | What runs the verified tier (spec 022). `visual` needs `GEMINI_API_KEY` and `LIGHTON_API_KEY`; the worker refuses to start without them. |
| `VERIFIER_MAX_SUSPECTS` | int | `20` | Suspect words verified per page, strongest first. Each costs up to ~9 paid calls. |
| `TTS_NARRATOR` | enum | `female` | The existing `walidapro` path synthesizes **both** narrators (`run_full_pipeline.py:1057-1130`); V1 needs exactly one artifact. `Q-08b`. |
| `ALIGNMENT_ENGINE` | enum | `whisper` | `whisper` (forced) or `native` (Hume). Native is measurably better — 93.7% vs 51.0% anchored on real books (`README.md:473,491`) — but Hume quota was exhausted historically. |
| `TTS_ENGINE` | enum | `gemini` | |
| `OCR_ENGINE` | enum | `auto` | `auto` prefers the free text-layer path when present (25 of 36 sample books — `README.md:444-448`), else the configured OCR engine |

Six flags, each justified by a real either/or in the existing code. No general-purpose flag framework.

### 2.3 Existing keys, harvested from the code

Confirmed by `grep -rn 'os.getenv\|os.environ'`. **All model ids move here from hardcoded constants**
([08-ai-integration.md](08-ai-integration.md) §6).

| Key | Current default | Defined at | Notes |
|---|---|---|---|
| `LIGHTON_BASE_URL` | `http://localhost:8000/v1` | `walidapro/ocr_verifier/config.py:12` | **Misconfigured as shipped.** The client appends `/api/v3/parse` (`lighton_ocr.py:23-26`), building `http://localhost:8000/v1/api/v3/parse`. The variable is set in **no** `.env`, `.env.example` or script in the repo. The parallel implementation hardcodes the correct hosted value `https://api.lighton.ai/api/v3/parse` (`src/pipeline/ocr_lighton.py:41`). **Deployment must set this explicitly.** |
| `LIGHTON_API_KEY` | `EMPTY` | `config.py:13` | secret |
| `LIGHTON_MODEL` | `lightonai/LightOnOCR-2-1B` | `config.py:14` | Dead for the hosted path — never sent in the payload; only stirred into a cache fingerprint at `pipeline.py:89` |
| `LIGHTON_MAX_SIDE` | `1540` | `config.py:22` | |
| `LIGHTON_MAX_TOKENS` | `6500` | `config.py:23` | Dead for the hosted path |
| `REVIEWER_BASE_URL` | `http://localhost:8000/v1` | `config.py:16` | `walidapro/.env.example` suggests `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `REVIEWER_API_KEY` | `EMPTY` | `config.py:17` | secret |
| `REVIEWER_MODEL` | **`""` — no default** | `config.py:18` | Both reviewer classes **raise in `__init__`** if it is empty (`reviewer.py:27-31`, `visual_reviewer.py:160-164`), and the pipeline constructs them eagerly (`pipeline.py:42-43`). A misconfigured deployment therefore fails at object construction rather than surfacing a health-check failure. |
| `FINAL_REVIEWER_API_KEY` | falls back to `REVIEWER_API_KEY` | `final_context_reviewer.py:221-224` | secret |
| `GEMINI_API_KEY` | — | `src/pipeline` modules | secret |
| `HUME_API_KEY` | — | `src/pipeline/tts_hume.py` path | secret |
| `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` | — | `tools/context_detector.py` | Dev only — the correction tools are not in V1 |
| `HF_TOKEN` | — | model download | secret |
| `OCR_DPI` | `220` | `config.py:20` | |
| `API_TIMEOUT` | `180` | `config.py:24` | Superseded by the explicit per-operation timeouts |
| `API_RETRIES` | `3` | `config.py:25` | Superseded by the shared retry policy |
| `SUSPICION_THRESHOLD` | `0.75` | `config.py:27` | Verified tier |
| `ACCEPT_THRESHOLD` | `0.88` | `config.py:28` | Verified tier |
| `REVIEW_THRESHOLD` | `0.70` | `config.py:29` | Verified tier |
| `CROP_PADDING_X` / `CROP_PADDING_Y` | `30` / `20` | `config.py:30-31` | Verified tier |
| `PADDLE_DEVICE` | `cpu` | `config.py:32` | **Proof that no GPU is needed.** Setting it to `gpu` would need a CUDA `paddlepaddle` build that nothing in this repo pins. |
| `DATALAB_API_KEY`, `BIADU_API_KEY`, `SALIMA_API_KEY` | — | `.env` only | **Dead keys from abandoned `tmp/` spikes. Revoke them.** |
| `BOOK`, `PAGES`, `VOICE` | — | several `main()`s read these | Dev CLI only; must not influence the service |

New model-id keys replacing hardcoded constants:

| New key | Replaces | Default |
|---|---|---|
| `OCR_GEMINI_MODEL` | `MODEL` at `src/pipeline/ocr_gemini.py:28` | `gemini-3.6-flash` |
| `TTS_GEMINI_MODEL` | `MODEL` at `src/pipeline/tts_gemini.py:16` | `gemini-3.1-flash-tts-preview` |
| `TTS_GEMINI_VOICE` | `VOICE` at `src/pipeline/tts_gemini.py:17` | `Kore` |
| `TTS_HUME_VOICE` | `VOICE` at `src/pipeline/tts_hume.py:12` | `Layla, Arabic Philosopher` |
| `FINAL_REVIEWER_MODEL` | `model` at `walidapro/.../final_context_reviewer.py:246` | `gemini-3.1-pro-preview` |
| `ALIGN_WHISPER_MODEL_SIZE` | `"small"` at `src/pipeline/align_whisper.py:19` | `small` |
| `ALIGN_WHISPER_DEVICE` | `"cpu"` at `align_whisper.py:22` | `cpu` |
| `ALIGN_WHISPER_COMPUTE_TYPE` | `"int8"` at `align_whisper.py:22` | `int8` |
| `DIAC_THRESHOLD` | `0.15` at `run_extract.py:26`, `run_ocr_gemini.py:28`, `run_ocr_lighton.py:33` | `0.15` — note it is currently duplicated in three files |

## 3. Why `load_dotenv()` must go

Two facts in the current code combine into a silent failure:

1. `load_dotenv()` runs **at module import** in 11 modules — `src/pipeline/run_ocr_gemini.py:20`,
   `run_ocr_lighton.py:25`, `tools/vlm_arbiter.py:28`, `walidapro/ocr_verifier/config.py:7`,
   `walidapro/ocr_verifier/final_context_reviewer.py:14`, and six more.
2. A real `.env` with eight live credentials sits in the repository root.

So: **if a `.env` is present in the container's working directory, importing the pipeline silently
overrides orchestrator-injected secrets.** A key rotated in the environment is shadowed by a stale key
in a file, with no log line and no error.

Required for V1 (`T-1.2`):

- No `load_dotenv()` anywhere in `ai_engine/`.
- The committed `.env` is deleted, `.gitignore`d, and **all eight keys rotated** — `git rm` does not
  remove it from history, so they must be treated as compromised.
- Because legacy modules still call `load_dotenv()` at import, a startup assertion verifies that **no
  `.env` exists on the container filesystem**, and refuses to start if one does. That converts a silent
  override into a loud failure.

## 4. `.env.example`

Placeholders only. Never a real credential.

```text
# ─── Role and server ──────────────────────────────────────────────────────────
AI_ENGINE_ROLE=api                     # api | worker | reconciler
AI_ENGINE_PORT=9099                    # matches the PDF's AI_Engine_BASE_URL example
AI_ENGINE_LOG_LEVEL=INFO

# ─── Backend integration (REQ-CFG-01) ─────────────────────────────────────────
BACKEND_BASE_URL=https://backend.internal:6060
TOKEN_URL=https://keycloak.internal:9090/realms/reading-first/protocol/openid-connect/token
AI_ENGINE_CLIENT_ID=reading-first-ocr-engine
AI_ENGINE_CLIENT_SECRET=__REPLACE_ME__

# ─── Inbound token validation (BLOCKED ON Q-01) ───────────────────────────────
# JWKS_URL defaults to TOKEN_URL with /token replaced by /certs
JWKS_URL=
BACKEND_ALLOWED_CLIENT_IDS=__REPLACE_ME__     # the Keycloak client the Backend presents
JWT_ALGORITHMS=RS256                          # RS256 only — never add HS256

# ─── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql://ai_engine:__REPLACE_ME__@postgres:5432/ai_engine
DB_POOL_SIZE=10

# ─── Storage ──────────────────────────────────────────────────────────────────
SCRATCH_DIR=/var/tmp/ai-engine
MODEL_CACHE_DIR=/var/cache/ai-engine/models    # MUST be a persistent volume (464 MB of weights)
MAX_SOURCE_BYTES=209715200
MAX_SOURCE_PAGES=500

# ─── Security: SSRF control (REQUIRED) ────────────────────────────────────────
SOURCE_URL_ALLOWED_HOSTS=storage.internal,s3.eu-west-1.amazonaws.com

# ─── Concurrency, deadlines, retries ──────────────────────────────────────────
WORKER_JOB_CONCURRENCY=2
WORKER_PAGE_CONCURRENCY=4
JOB_PAGE_CONCURRENCY=4
WORKER_LEASE_SECONDS=300
PAGE_TASK_TIMEOUT_SECONDS=900
JOB_DEADLINE_SECONDS=86400
PAGE_MAX_ATTEMPTS=3
JOB_MAX_ATTEMPTS=3
CALLBACK_MAX_ATTEMPTS=10
RECONCILE_INTERVAL_SECONDS=60
RETENTION_DAYS_TERMINAL_JOBS=90
HTTP_CONNECT_TIMEOUT_SECONDS=5
HTTP_READ_TIMEOUT_SECONDS=60

# ─── Feature flags ────────────────────────────────────────────────────────────
DEFAULT_QUALITY_TIER=fast
ENABLE_VERIFIED_TIER=false             # hard kill-switch: 40-100x the paid calls per page
OCR_ENGINE=auto                        # auto prefers the free text-layer path
TTS_ENGINE=gemini
TTS_NARRATOR=female                    # V1 uploads exactly one Audio artifact
ALIGNMENT_ENGINE=whisper               # whisper (forced) | native (Hume word timestamps)

# ─── Model configuration (was hardcoded constants) ────────────────────────────
OCR_GEMINI_MODEL=gemini-3.6-flash
TTS_GEMINI_MODEL=gemini-3.1-flash-tts-preview
TTS_GEMINI_VOICE=Kore
TTS_HUME_VOICE=Layla, Arabic Philosopher
FINAL_REVIEWER_MODEL=gemini-3.1-pro-preview
ALIGN_WHISPER_MODEL_SIZE=small
ALIGN_WHISPER_DEVICE=cpu               # no GPU is required anywhere in V1
ALIGN_WHISPER_COMPUTE_TYPE=int8
DIAC_THRESHOLD=0.15

# ─── AI provider credentials ──────────────────────────────────────────────────
GEMINI_API_KEY=__REPLACE_ME__
LIGHTON_BASE_URL=https://api.lighton.ai   # NOTE: the client appends /api/v3/parse
LIGHTON_API_KEY=__REPLACE_ME__
HUME_API_KEY=
HF_TOKEN=__REPLACE_ME__                   # first-run model download only

# ─── Verified tier only (ENABLE_VERIFIED_TIER=true; spec 022) ─────────────────
VERIFIER=visual                           # visual | passthrough
VERIFIER_GEMINI_MODEL=gemini-3.6-flash    # uses GEMINI_API_KEY; crop re-reads use LIGHTON_API_KEY
VERIFIER_SUSPICION_THRESHOLD=0.75
VERIFIER_ACCEPT_THRESHOLD=0.88
VERIFIER_REVIEW_THRESHOLD=0.70
VERIFIER_MAX_SUSPECTS=20                  # bounds per-page verification spend
FINAL_REVIEW=off                          # off | suggest (spec 024): proposals become REVIEW verdicts
FINAL_REVIEW_MODEL=gemini-3.1-pro-preview # unpriced in config/prices.toml: add its rate first

# ─── Provider rate budgets (cost + real free-tier walls) ──────────────────────
PROVIDER_RATE_BUDGET_GEMINI=1000
PROVIDER_RATE_BUDGET_LIGHTON=1000
```
