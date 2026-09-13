# Repository Structure

## 1. Guiding decision

> **Implementation note (as built).** The service package landed at **`service/ai_engine/`**
> with its own `pyproject.toml`, rather than `ai_engine/` at the repository root as the tree
> below proposes. The reason is containment: `service/` is self-contained and installable on
> its own, and adding it touched **zero** existing files — the pre-existing 56 tests still
> pass unmodified. Adapters reach the legacy pipelines through the `LEGACY_REPO_ROOT`
> setting instead of a shared root package. Everything else in this document — the layer
> responsibilities, the import rules, the file dispositions — applies unchanged; read
> `ai_engine/` below as `service/ai_engine/`.
>
> Two consequences of that containment, both visible in the built tree:
> `Dockerfile` and `docker-compose.yml` live in `service/`, but their **build context is the
> repository root**, because the adapters import `src/pipeline` — the root `.dockerignore`
> is what keeps 895 MB of `assets/` and `tmp/` out of that context. And CI is at
> `.github/workflows/service-ci.yml`, gating **both** suites: breaking `src/tests` breaks
> this service.


**Add a new service package alongside the existing trees; import them, do not rewrite them.** This is
Rule 2 of the parent brief and it is also the lowest-risk path: 71% of production LOC is salvageable
domain logic ([04-gap-analysis.md](04-gap-analysis.md) §2), and the 56 existing tests must stay green
throughout.

The existing trees keep their paths. `ai_engine/` is new.

```text
ocr_review_models/
├── pyproject.toml                    # NEW — the repo becomes installable for the first time
├── uv.lock  (or requirements.lock)   # NEW — pinned, reproducible
├── alembic.ini                       # NEW
├── Dockerfile                        # NEW — one image, three entrypoints
├── docker-compose.yml                # NEW
├── Makefile                          # NEW — test / lint / migrate / up
├── .env.example                      # REPLACED (see 15-configuration.md)
├── .dockerignore                     # NEW
│
├── ai_engine/                        # ══ NEW: the whole V1 service ══
│   ├── __init__.py
│   ├── main.py                       # entrypoint dispatch on AI_ENGINE_ROLE
│   │
│   ├── api/                          # HTTP layer
│   │   ├── app.py                    # FastAPI app factory, middleware wiring
│   │   ├── routers/jobs.py           # POST /api/jobs, GET /api/jobs/{jobId}
│   │   ├── routers/ops.py            # /health, /ready, /metrics
│   │   ├── auth.py                   # JWKS cache, RS256 validation, azp allowlist
│   │   ├── errors.py                 # exception handlers -> the error envelope
│   │   └── schemas/                  # request/response pydantic models ONLY
│   │
│   ├── application/                  # use cases — one class per business operation
│   │   ├── accept_job.py             # AcceptJob: validate, dedupe, persist, 202
│   │   ├── run_extraction_job.py     # Job A orchestration
│   │   ├── run_audio_job.py          # Job B orchestration
│   │   ├── run_page_task.py          # the Page Task Executor
│   │   ├── reconcile_jobs.py         # INVARIANT-1 enforcement
│   │   ├── drain_outbox.py           # callback delivery
│   │   └── dto.py                    # application DTOs (10-data-contracts.md §3)
│   │
│   ├── domain/                       # pure model — STDLIB IMPORTS ONLY
│   │   ├── job.py                    # Job, JobType, JobState
│   │   ├── page.py                   # PageTask, PageState
│   │   ├── artifact.py               # Artifact, ArtifactType
│   │   ├── tokens.py                 # Token, TimedToken, TimingSource, BindReport
│   │   ├── errors.py                 # ErrorCode, FailureReason, the exception hierarchy
│   │   └── invariants.py             # INVARIANT-1 predicates, pure and unit-testable
│   │
│   ├── ports/                        # abstract interfaces — NO implementations
│   │   ├── ocr.py  diacritizer.py  tts.py  aligner.py  page_verifier.py
│   │   ├── renderer.py  source_fetcher.py  uploader.py  callbacks.py
│   │   └── job_store.py
│   │
│   ├── adapters/                     # the ONLY place SDKs and legacy code are imported
│   │   ├── ai/
│   │   │   ├── ocr_textlayer.py      # wraps src/pipeline/extract.py
│   │   │   ├── ocr_gemini.py         # wraps src/pipeline/ocr_gemini.py
│   │   │   ├── ocr_lighton.py        # wraps src/pipeline/ocr_lighton.py
│   │   │   ├── diacritizer_local.py  # wraps src/pipeline/tashkel.py
│   │   │   ├── diacritizer_visual.py # wraps walidapro diacritization (verified tier)
│   │   │   ├── tts_gemini.py  tts_hume.py
│   │   │   ├── aligner_whisper.py    # wraps align_whisper + speak.bind VERBATIM
│   │   │   ├── renderer_pdf.py       # streaming page render + page_count
│   │   │   └── verifier_walidapro.py # wraps walidapro process_page + FinalContextReviewer
│   │   ├── backend/
│   │   │   ├── token_provider.py     # Keycloak client_credentials, single-flight
│   │   │   ├── files_client.py       # the 3-step upload cycle + get-download-url
│   │   │   └── jobs_client.py        # metadata / completed / failed PATCHes
│   │   ├── storage/
│   │   │   ├── source_fetcher.py     # presigned GET, expiry refresh, SSRF guard
│   │   │   └── scratch.py            # per-job workspace lifecycle
│   │   └── persistence/
│   │       ├── models.py             # SQLAlchemy tables
│   │       ├── repositories.py       # JobStore, PageLedger, ArtifactLedger, Outbox
│   │       └── migrations/           # alembic versions
│   │
│   ├── artifacts/                    # the two NEW projections
│   │   ├── text_builder.py           # tokens -> text/plain bytes
│   │   ├── timing_builder.py         # tokens + bind_report -> the timing JSON
│   │   └── tokenizer.py              # the DETERMINISTIC shared tokenizer (Job A and Job B)
│   │
│   ├── observability/
│   │   ├── logging.py                # structlog config + redaction filter
│   │   ├── metrics.py                # the metric registry from 13-observability.md §3
│   │   └── context.py               # job_id / page_number contextvars
│   │
│   ├── config/settings.py            # pydantic-settings; NO load_dotenv
│   └── cli/                          # dev-only commands (run one book locally, inspect a job)
│
├── src/pipeline/                     # KEPT — imported by adapters. + __init__.py
├── tools/                            # KEPT as offline/human tooling. + __init__.py
├── walidapro/                        # KEPT — verified tier only
│
├── tests/                            # NEW — the service test suite
│   ├── unit/  integration/  api/  ai/  e2e/  performance/
│   ├── fakes/                        # the contract fakes: backend, keycloak, s3
│   └── golden/                       # real artifacts copied from work/ as fixtures
├── src/tests/                        # KEPT VERBATIM — the 56-test regression net
│
├── docs/backend-v1/                  # this document set
└── work/  assets/  demo/             # KEPT — local corpus and dev artifacts
```

## 2. Directory responsibilities

| Directory | Responsibility | Belongs here | Must **NOT** be here | Layer |
|---|---|---|---|---|
| `ai_engine/api/` | Terminate HTTP. Authenticate, validate, serialize. | Routers, middleware, request/response schemas, JWT validation, exception handlers | Any business rule; any adapter; any provider SDK; `src.pipeline`; `walidapro` | API |
| `ai_engine/application/` | Orchestrate one business operation. | Use-case classes, transaction boundaries, DTOs, the page-task lifecycle | FastAPI types, HTTP status codes, SQL, concrete adapters (they arrive by injection) | Application |
| `ai_engine/domain/` | Express the model and its rules. | Entities, enums, value objects, the error hierarchy, pure invariant predicates | **Anything non-stdlib.** No pydantic, no SQLAlchemy, no requests. If `pip install nothing` cannot import this package, it is wrong. | Domain |
| `ai_engine/ports/` | Declare what the application needs. | `Protocol` classes, their DTOs, docstring contracts, declared exceptions | Any implementation, any SDK import | Port |
| `ai_engine/adapters/` | Implement the ports against real systems. | Provider SDK calls, HTTP clients, SQL, legacy-module imports, retry and timeout wiring | Business rules; anything from `api/` or `application/` | Adapter |
| `ai_engine/artifacts/` | Project internal data into the four deliverables. | The text builder, the timing builder, the deterministic tokenizer | Uploading (that is `adapters/backend/`), AI inference | Application |
| `ai_engine/observability/` | Logging, metrics, correlation. | Logger config, redaction, the metric registry, contextvars | Business logic; anything that can raise into a job | Cross-cutting |
| `ai_engine/config/` | Read and validate the environment. | `pydantic-settings` models | `load_dotenv()`; module-level singletons; defaults that hide a missing secret | Cross-cutting |
| `ai_engine/cli/` | Developer conveniences. | Local one-book runs, job inspection | Anything the service depends on at runtime | — |
| `src/pipeline/` | The AI pipeline libraries. | The existing pure functions and provider clients | New service concerns; job ids; HTTP; callbacks | Adapter target |
| `tools/` | Offline and human-in-the-loop tooling. | The correction chain, the HTML reviewers | Anything imported by `ai_engine/` at runtime | — |
| `walidapro/` | The verified tier's AI logic. | The existing verification stack | New orchestration | Adapter target |
| `tests/` | The service test suite. | Unit, integration, API, AI, e2e, performance; the contract fakes | Real network calls in the default suite | — |
| `src/tests/` | The existing regression net. | The 56 tests, unchanged | New service tests | — |

### Enforceable import rules (`T-1.6`)

A lint check (`import-linter` or a `ruff` custom rule) fails the build on any violation:

```text
api          → application, domain, api.schemas
application  → domain, ports
domain       → stdlib ONLY
ports        → domain
adapters     → ports, domain, external SDKs, src.pipeline, walidapro
artifacts    → domain
observability→ (imported by all; imports domain only)

FORBIDDEN:  api → adapters          # the API must never touch a model implementation
FORBIDDEN:  api → src.pipeline      # or walidapro
FORBIDDEN:  application → adapters  # concrete adapters arrive by injection
FORBIDDEN:  domain → anything third-party
FORBIDDEN:  adapters → api, application
```

The first two forbidden edges are the ones that matter for security as well as design: they are what
guarantee no AI code path is reachable from an HTTP handler ([12-security.md](12-security.md) §7).

## 3. Disposition of every existing file

### `src/pipeline/`

| Path | Current role | Disposition | Target | Why |
|---|---|---|---|---|
| `extract.py` | text-layer extraction, `clean_word` 4 rules | **stay + wrap** | wrapped by `adapters/ai/ocr_textlayer.py` | Pure, free, unit-tested with controls |
| `pages.py` | 1-based page-spec parser | **stay** | reused | 30 pure lines; source of the 1-based convention |
| `speak.py` | `bind`, `_match_forward`, `_interpolate` | **stay, unchanged** | wrapped by `adapters/ai/aligner_whisper.py` | The single most valuable reusable function in the repo. Do not touch it. |
| `align_whisper.py` | faster-whisper alignment | **stay + refactor** | same | Remove the module-global `_model` (`:16-23`); parameterize `device`/`compute_type` (`:22`) |
| `tashkel.py` | local diacritizer | **stay + refactor** | same | Remove the module-global `_model` (`:26-33`) |
| `ocr_gemini.py` | Gemini OCR + `render_page` | **stay + wrap** | `adapters/ai/ocr_gemini.py`, `renderer_pdf.py` | Model id to config; explicit timeout; status-aware retry |
| `ocr_lighton.py` | LightOn client | **stay + wrap** | `adapters/ai/ocr_lighton.py` | Add retry (there is none at `:66`); base URL to config |
| `tts_gemini.py`, `tts_hume.py` | TTS clients | **stay + wrap** | `adapters/ai/tts_*.py` | Return bytes, not files; add timeouts |
| `main.py` | pipeline CLI | **deprecate as a service path; keep as a dev CLI** | — | Superseded by `RunExtractionJob` |
| `run_extract.py`, `run_ocr_gemini.py`, `run_ocr_lighton.py`, `run_tashkel.py`, `run_speak_gemini.py`, `run_speak_hume.py` | book-level drivers | **deprecate** | replaced by `run_page_task.py` | `break`/`return` on error violates INVARIANT-1; no per-page hook exists |
| `build_reader.py` | `reader.html` generator | **keep as a dev CLI** | — | Human artifact; no `artifactType` for HTML (`Q-11`) |
| *(new)* `__init__.py` | — | **add** | — | Required for real packaging |

### `tools/`

| Path | Disposition | Why |
|---|---|---|
| `ensemble_review.py` | **keep; extract `normalize_for_compare:34` and `align_page:62` as reusable pure functions** | Tested, dependency-free, real Arabic domain knowledge. Useful for a future consensus tier. |
| `apply_corrections.py`, `apply_context_suggestions.py`, `replace_page.py` | **keep; harvest the merge semantics** | `ocr_txt` preservation, `fixes[]` append, letter-only staleness check — the correct correction model |
| `vlm_arbiter.py` | **keep as offline tooling** | Automatable, but belongs to the two-engine flow V1 does not run |
| `context_detector.py`, `build_context_review.py` | **keep as offline tooling; explicitly out of V1** | **Human-gated by design**: `context_detector.py:148-160` defaults every decision to `"reject"` |
| `build_review.py` | **keep as offline tooling** | A/B review page for a human to listen to |
| `run_full_pipeline.py` | **keep as offline tooling** | `:99-115` prints *"STOPPING HERE ON PURPOSE -- one manual step left"* |
| `export_session.py` | **move to `scripts/`** | Claude Code transcript → Markdown. Unrelated developer tooling that reads `~/.claude`. |
| *(new)* `__init__.py` | **add** | — |

### `walidapro/`

| Path | Disposition | Why |
|---|---|---|
| `ocr_verifier/decision.py`, `models.py` | **stay, unchanged** | Pure fusion function; serviceable DTOs |
| `ocr_verifier/utils.py` | **stay; promote `atomic_write_text:48`** | The tmp-file + `os.replace` pattern the rest of the repo lacks |
| `ocr_verifier/pdf_render.py` | **stay + refactor** | Add a streaming generator and return `page_count`; today `:41-107` materializes every page as PIL RGB |
| `ocr_verifier/cropper.py` | **stay + refactor** | Add a disk-free path; `_threshold:101` materializes every pixel into a Python list |
| `ocr_verifier/lighton_ocr.py`, `reviewer.py`, `visual_reviewer.py` | **stay + wrap** | Verified tier. Fix retry classification and blocking sleeps. |
| `ocr_verifier/final_context_reviewer.py` | **stay + wrap** | Fix double-retry amplification (`:807`/`:251` vs SDK `max_retries=2`) and the missing timeout (`:825`); replace ~20 `print()`s |
| `ocr_verifier/pipeline.py` | **refactor: keep `process_page:863`, delete `run_pdf:1796` and `save_outputs:1873`** | The former is the asset; the latter two are document-batch and emit nothing until the last page |
| `ocr_verifier/diacritization/*` | **refactor** | Keep the DP aligner, the 0.72 gate, the letters-unchanged check and `rebuild_text`'s two assertions. Replace the serial per-word loop and the lock-free cache. |
| `ocr_verifier/tts/*` | **stay + wrap** | Keep `save_wav`, `merge_wav_files`, `TextChunker.chunk`, the narration prompts, `_duration_is_sane` |
| `ocr_verifier/bbox_detector.py` | **stay + refactor** | Must be a shared instance; **loaded twice today** (`pipeline.py:44` and `word_diacritics.py:93`) |
| `ocr_verifier/pdf_type_detector.py` | **delete** | Verified dead — nothing imports it |
| `ocr_verifier/config.py` | **delete from the service path** | Frozen import-time singleton with `load_dotenv()`. Replaced by `ai_engine/config/settings.py`. |
| `run.py`, `run_full_pipeline.py` | **deprecate** | CLI orchestration incl. the per-page subprocess fork at `:503-529` |
| `diacritization/run_word_diacritics_test.py`, `tts/run_tts_test.py`, `tts/test_voices.py` | **move to `scripts/`** | Ad-hoc manual scripts, not tests (`test_voices.py` has no test functions; `pytest walidapro` collects nothing) |
| `ocr_verifier/*.jpg`, `*.png` | **delete** | Stray sample images committed into a source package |
| `FUNCTIONAL_AUDIT_REPORT.txt`, `AUDIT_REPORT_AR.txt` | **move to `docs/history/`** | Valuable prior art; they document deliberate safety fixes |

### Repo root and data directories

| Path | Disposition | Why |
|---|---|---|
| `README.md` (43 KB) | **keep; add a pointer to `docs/backend-v1/`** | Genuinely good; it is the source of several measured figures cited throughout |
| `TESTING.md` | **keep; fold into `docs/`** | Accurately documents what is and is not tested |
| `SESSION.md` (740 KB) | **delete or move to `docs/history/`** | A raw transcript. It bloats every clone and every container build context. |
| `high_tasks.txt` | **delete** | A 3-line personal scratch note |
| `requirements.txt` | **replace with `pyproject.toml` + a lock file** | 15 bytes declaring 1 of 14 real dependencies, with zero pins |
| `pytest.ini` | **fold into `pyproject.toml`**; keep `src/tests` in `testpaths` and add `tests` | Must not lose the existing suite |
| `.env` | **delete from the working tree, `.gitignore`, and rotate all eight keys** | See [12-security.md](12-security.md) §4. `git rm` does not purge history. |
| `.ruff_cache/` | **delete; add a real ruff config** | Stale cache for a tool that is not installed and has no config |
| `__pycache__/`, `.pytest_cache/` | **delete; `.gitignore`** | — |
| `tmp/` (~1,400 LOC, 115 MB) | **move to `spikes/` or delete** | Ten abandoned vendor spikes. `tmp/dotocr/book.json` uses an incompatible schema. Nothing imports any of it. |
| `demo/` (34 MB) | **keep** | A self-contained HTML proof of concept; `pipeline_poc.html` is the only fully self-contained artifact in the repo |
| `models/` | **delete the entry from `.gitignore` if the directory does not exist** | Referenced by `.gitignore` but not present |
| `work/` (11 book folders) | **keep locally; add to `.dockerignore`** | The golden-fixture corpus for `tests/golden/`. Already partly `.gitignore`d (`*.wav`, `work/*/audio/`, `work/*/pages/`), so only 107 files are tracked. |
| `assets/PDF Samples` (780 MB) | **keep locally; `.dockerignore`** | 35 sample PDFs — the integration-test corpus. Must never enter a container image. |

## 4. Packaging

Current state: **no `pyproject.toml`, no `setup.py`, no `__init__.py`** in `src/`, `src/pipeline/`,
`tools/` or `src/tests/`. Imports resolve only via PEP-420 namespace packages plus `sys.path.insert`
hacks (`src/tests/conftest.py:18-21`) and a fortunate working directory. There is no unit of deployment.

```toml
[project]
name = "reading-first-ai-engine"
version = "1.0.0"
requires-python = ">=3.11"        # 3.11.15 is the interpreter the 56 tests pass under today

dependencies = [
  # existing pipeline — every version MUST be pinned in the lock file
  "pypdfium2", "pymupdf", "pypdf", "pillow", "numpy",
  "requests", "openai", "google-genai", "hume",
  "faster-whisper", "arabic-diacritizer",
  # new service layer
  "fastapi", "uvicorn[standard]", "pydantic>=2", "pydantic-settings",
  "sqlalchemy>=2", "alembic", "psycopg[binary]",
  "httpx", "pyjwt[crypto]", "tenacity",
  "prometheus-client", "structlog",
]

[project.optional-dependencies]
verified = ["paddleocr", "paddlepaddle"]         # verified tier only — large, CPU build
dev = ["pytest", "pytest-asyncio", "pytest-cov", "ruff", "import-linter",
       "respx", "testcontainers[postgres]"]

[project.scripts]
ai-engine-api        = "ai_engine.main:run_api"
ai-engine-worker     = "ai_engine.main:run_worker"
ai-engine-reconciler = "ai_engine.main:run_reconciler"
ai-engine-migrate    = "ai_engine.main:run_migrations"

[tool.pytest.ini_options]
testpaths = ["tests", "src/tests"]               # the 56 existing tests stay in the run
python_files = ["test_*.py"]
asyncio_mode = "auto"                            # src/pipeline/run_speak_hume.py is async and untestable today
```

Two deliberate choices:

- **`paddleocr` is an optional extra.** It is only needed by the verified tier, it is a large
  dependency, and making it optional keeps the default image small and the default install fast.
- **`asyncio_mode = "auto"`.** `pytest.ini` has no asyncio configuration today, which is why
  `src/pipeline/run_speak_hume.py` — the one async module in the repo — cannot be tested at all.

Every version must be pinned in the lock file. Today there are **zero** pins, so two developers on the
same commit can have different Gemini SDK behaviour.

## 5. Migration sequence

The rule at every step: **`pytest src/tests` stays green.** Those 56 tests are the only safety net that
exists.

| Step | Action | Green check |
|---|---|---|
| 1 | Add `pyproject.toml` with the real dependency set; add `__init__.py` to `src/`, `src/pipeline/`, `tools/`; keep `sys.path` hacks working | `pytest src/tests` → 56 passed |
| 2 | Add a lock file; verify a clean-venv install | 56 passed in a fresh venv |
| 3 | Add `ai_engine/` skeleton with `domain/`, `config/`, `observability/` and the import-linter contract | 56 passed; lint clean |
| 4 | Remove the `sys.path.insert` hacks now that real packages exist; update `conftest.py:18-21` | 56 passed |
| 5 | Add `ports/` and the first adapters; **no changes to the wrapped modules yet** | 56 passed |
| 6 | Refactor the two module-global model singletons (`align_whisper.py:16-23`, `tashkel.py:25-33`) to accept an injected instance, keeping a backward-compatible default | 56 passed |
| 7 | Convert the 19 `SystemExit` sites in library functions to typed errors — **and update the two tests that assert `SystemExit`** (`test_ensemble_review.py:143`, `test_apply_context_suggestions.py:105`) | 56 passed, 2 modified |
| 8 | Remove `load_dotenv()` from any module `ai_engine/` imports; add the no-`.env`-on-disk startup assertion | 56 passed |
| 9 | Add the streaming renderer and `page_count`; add the two artifact projections | 56 passed + new tests |
| 10 | Move `SESSION.md`, `tmp/`, the audit reports, and the ad-hoc `walidapro` test scripts | 56 passed |
| 11 | Delete `walidapro/ocr_verifier/pdf_type_detector.py` and the stray committed images | 56 passed |

Steps 1–4 are pure additions and can land immediately. Step 7 is the only one that changes existing test
expectations, and it should be a single reviewable commit.
