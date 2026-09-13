# Implementation Roadmap

Task IDs are `T-<phase>.<n>` and are referenced from
[03-requirements-to-code-map.md](03-requirements-to-code-map.md) and
[20-traceability-matrix.md](20-traceability-matrix.md). Effort is S / M / L, not days — day estimates
before a baseline exists would be fiction, and Phase 0 exists partly to produce that baseline.

## Sequencing rules

1. **Phase 0 first, always.** The repo is not installable, its dependency manifest declares 1 of 14
   packages, and there is **zero timing instrumentation anywhere**. Every latency figure in this document
   set is an estimate because of that last point. You cannot enforce a page deadline you cannot measure,
   and you cannot size a worker fleet from guesses.
2. **The vertical slice before the breadth.** Get **one page of Job A** all the way through to a real
   `completed` PATCH against the contract fake before building anything else out. The protocol is the
   actual unknown; the AI already works. A vertical slice fails fast on the thing most likely to be
   wrong.
3. **`pytest src/tests` stays green at every step.** Those 56 tests are the only safety net that exists.
4. **`Q-01` blocks `T-2.2`.** Nothing else in the plan is blocked on an open question.

## Critical path to the first joint integration test

```text
T-0.1 → T-1.1 → T-1.2 → T-1.3 → T-2.1 → T-2.3 → T-2.4 → T-4.1
      → T-5.1 → T-5.2 → T-5.3 → T-3.1 → T-3.2 → T-3.3 → T-3.4
      → T-5.5 → T-5.4 → T-5.6 → T-4.2 → T-4.4 → T-6.1 → T-7.1
```

Everything else — the verified tier, tracing, performance guards, Job B's audio path — can follow. Note
that `T-2.2` (inbound auth) is **not** on this path: the contract fake can issue tokens against a fake
realm, so the integration can be built while `Q-01` is still open.

## Suggested two-engineer split

| | Ahmed | Waleed |
|---|---|---|
| Phase 0–1 | `T-0.1`, `T-0.2`, `T-1.1`, `T-1.2`, `T-1.6` | `T-0.3`, `T-0.4`, `T-1.3`, `T-1.4`, `T-1.5`, `T-1.7` |
| Phase 2–4 | `T-2.*` (API, auth, accept), `T-4.1`, `T-4.6` | `T-3.*` (AI adapters, artifact projections) |
| Phase 4–5 | `T-4.2`, `T-4.4`, `T-4.5`, `T-5.1`, `T-5.2`, `T-5.6` | `T-4.3`, `T-5.3`, `T-5.5`, `T-5.7` |
| Phase 6–7 | `T-6.1` (the contract fake), `T-6.2`, `T-7.*` | `T-6.4`, `T-6.5`, `T-6.6` |

The split is chosen for low collision: the API/protocol side and the AI-adapter side touch almost
disjoint files, meeting only at the port definitions (`T-1.3`), which should therefore land early and
be reviewed by both.

---

# Phase 0 — Understanding and baseline

Parallelisable: mostly. `T-0.1` first.

### `T-0.1` — Pin the real dependency set and get a clean install *(S)*

- **Objective.** A fresh virtualenv can install the project and run the existing tests.
- **Affected.** `requirements.txt` (deleted), new `pyproject.toml`, new lock file.
- **Details.** Derive the true dependency set from imports: `pypdfium2`, `pymupdf`, `pypdf`, `pillow`,
  `numpy`, `requests`, `python-dotenv`, `openai`, `google-genai`, `hume`, `faster-whisper`,
  `arabic-diacritizer`, `pytest`, plus `paddleocr` as an optional `verified` extra. Pin every version.
- **Acceptance.** `pip install -e .` in a clean venv, then `pytest src/tests` → **56 passed**. Today
  `pip install -r requirements.txt` followed by any entry point fails immediately on
  `from dotenv import load_dotenv`.
- **Tests.** The existing 56, in a clean environment.

### `T-0.2` — Add packaging skeleton without breaking imports *(S)*

- **Objective.** The repo becomes importable as real packages.
- **Affected.** `__init__.py` in `src/`, `src/pipeline/`, `tools/`; `pyproject.toml` package discovery.
- **Details.** Add the files, keep the `sys.path.insert` hacks (`src/tests/conftest.py:18-21`) working
  for now; remove them in `T-1.7`.
- **Acceptance.** `python -c "import pipeline.speak"` works from any CWD. 56 passed.

### `T-0.3` — Add timing instrumentation to the existing pipeline *(M)*

- **Objective.** Replace estimates with measurements. **This task is why Phase 0 exists.**
- **Affected.** `src/pipeline/run_extract.py`, `run_ocr_*.py`, `run_tashkel.py`, `run_speak_*.py`,
  `align_whisper.py`; a small `timings.jsonl` writer.
- **Details.** Wrap each stage with `perf_counter` and emit
  `{book, page, stage, duration_ms, provider, model, calls}`. Also count paid API calls per page — there
  is **no cost instrumentation of any kind** today. Repo-wide, `time` is imported only for `sleep`.
- **Acceptance.** Running one book produces a `timings.jsonl` from which p50/p90 per stage per page can
  be computed. The numbers replace the estimates in
  [01-current-architecture.md](01-current-architecture.md) §5.
- **Tests.** `test_timing_record_written_per_stage`.

### `T-0.4` — Capture an end-to-end baseline on the real corpus *(S)*

- **Objective.** Know what "working" looks like before changing anything.
- **Affected.** none — a measurement run.
- **Details.** Run one text-layer book and one OCR book from `assets/PDF Samples` end to end. Record
  per-stage latency, paid-call counts, and per-page `anchor_ratio`. Compare against the corpus reference
  values already measured: **78.0% anchored / 21.8% interpolated / 0.2% untimed** across 95 pages.
- **Acceptance.** A baseline document exists with real numbers, and the `anchor_ratio` is within the
  known 0.51–0.94 per-book range.

---

# Phase 1 — Foundation

Parallelisable after `T-1.3`.

### `T-1.1` — `pyproject.toml`, entrypoints, dependency groups *(S)*

- **Objective.** One installable distribution with three console scripts.
- **Details.** `ai-engine-api`, `ai-engine-worker`, `ai-engine-reconciler`, `ai-engine-migrate`. Groups:
  default, `verified` (`paddleocr`, `paddlepaddle`), `dev`. `pytest` config folded in with
  `testpaths = ["tests", "src/tests"]` and `asyncio_mode = "auto"` — the latter because
  `src/pipeline/run_speak_hume.py` is the one async module in the repo and is currently untestable.
- **Acceptance.** All four scripts resolve and print `--help`. 56 passed.

### `T-1.2` — `pydantic-settings` configuration, and remove `load_dotenv` from the service path *(M)*

- **Objective.** Configuration is env-only, validated at startup, injected not global.
- **Affected.** New `ai_engine/config/settings.py`; the `.env` handling.
- **Details.** Implement every key in [15-configuration.md](15-configuration.md) §2 with types and
  validators. **No `load_dotenv()` in `ai_engine/`.** Add a startup assertion that no `.env` exists on
  the container filesystem — necessary because legacy modules call `load_dotenv()` at import in 11
  places, so a stray `.env` would silently override injected secrets. Delete the committed `.env`,
  `.gitignore` it, and **rotate all eight keys** (`git rm` does not purge history). Revoke the three
  dead keys (`DATALAB_API_KEY`, `BIADU_API_KEY`, `SALIMA_API_KEY`).
- **Acceptance.** Missing `AI_ENGINE_CLIENT_SECRET` aborts startup naming the key. `repr(settings)`
  shows no secret values. Startup refuses if a `.env` is present.
- **Tests.** `test_missing_required_secret_aborts_startup`, `test_settings_repr_redacts_secrets`,
  `test_startup_refuses_when_dotenv_present`.

### `T-1.3` — `domain/` and `ports/` *(M)*

- **Objective.** The stdlib-only core and the abstract interfaces. **Land this early — both engineers
  depend on it.**
- **Affected.** New `ai_engine/domain/`, `ai_engine/ports/`.
- **Details.** All entities and enums from [10-data-contracts.md](10-data-contracts.md) §4 with the exact
  wire values (`ArtifactType` = `Image`/`Text`/`Audio`/`JSON`); the ports from
  [08-ai-integration.md](08-ai-integration.md) §1; the INVARIANT-1 predicates as pure functions in
  `domain/invariants.py`.
- **Acceptance.** `python -c "import ai_engine.domain"` succeeds in an environment with **no third-party
  packages installed**. That is the test of whether the layering is real.
- **Tests.** The whole unit "Domain invariants" and "State machines" sets from
  [17-testing-strategy.md](17-testing-strategy.md) §3.1.

### `T-1.4` — Error taxonomy and the safe-reason builder *(M)*

- **Objective.** One error model, used in responses, logs, metrics and `reason` strings.
- **Affected.** `ai_engine/domain/errors.py`, `ai_engine/api/errors.py`.
- **Details.** All 15 codes from [14-error-model.md](14-error-model.md) §1 with HTTP mapping and
  retryable flags; the exception hierarchy; the error envelope; and `FailureReason`, which **redacts at
  construction** and caps at 512 characters, so no code path can build an unsafe reason.
- **Acceptance.** A reason containing a presigned URL comes out redacted. Every code has exactly one
  HTTP mapping and one retryable classification.
- **Tests.** `test_every_error_code_has_http_mapping`, `test_reason_never_contains_url_or_token`,
  `test_reason_string_is_bounded_to_512`.

### `T-1.5` — Convert library `SystemExit` sites to typed errors *(M)*

- **Objective.** A library function can never kill a worker.
- **Affected.** 19 sites across `tools/` and `src/pipeline/`; notably
  `tools/ensemble_review.py:133` (inside `run()`, not `main()`).
- **Details.** Raise typed domain errors; keep `sys.exit` only in CLI `main()` functions. **Two existing
  tests assert `SystemExit`** (`src/tests/test_ensemble_review.py:143`,
  `src/tests/test_apply_context_suggestions.py:105`) and must be updated in the same commit. `SystemExit`
  inherits `BaseException`, so today it escapes `except Exception` handlers.
- **Acceptance.** `grep -rn 'sys.exit\|SystemExit' src/pipeline tools` shows hits only inside `main()`.
  56 tests pass with 2 modified.
- **Tests.** `test_adapter_never_raises_systemexit`.

### `T-1.6` — Structured logging, metrics registry, import-layer lint *(M)*

- **Objective.** Observability primitives, and a machine-enforced layering contract.
- **Affected.** New `ai_engine/observability/`; `.importlinter`.
- **Details.** `structlog` JSON to stdout with the schema in
  [13-observability.md](13-observability.md) §2, including the redaction filter; `job_id`/`page_number`
  contextvars; the metric registry with the exact names from §3. Add the `import-linter` contract from
  [16-repository-structure.md](16-repository-structure.md) §2 — in particular **forbid `api → adapters`
  and `api → src.pipeline`/`walidapro`**, which is what guarantees no AI code is reachable from an HTTP
  handler.
- **Acceptance.** `lint-imports` passes and fails on a deliberately-introduced violation. A log line
  containing a token is emitted redacted.
- **Tests.** `test_no_ai_module_importable_from_api_process`, `test_logger_redacts_secrets`.

### `T-1.7` — Remove the `sys.path` hacks *(S)*

- **Objective.** Imports resolve through real packaging.
- **Affected.** `src/tests/conftest.py:18-21` and the per-module hacks.
- **Acceptance.** 56 passed with no `sys.path` manipulation anywhere.

---

# Phase 2 — Backend / API layer

Depends on `T-1.2`, `T-1.3`, `T-1.4`, `T-4.1`.

### `T-2.1` — FastAPI app, ops endpoints *(S)*

- **Objective.** A serving process.
- **Details.** App factory, middleware (request id, logging, exception handlers), `GET /health`
  (**no dependency checks — always 200 while alive**), `GET /ready` (role-aware), `GET /metrics`.
  Provider reachability is deliberately **not** a readiness signal
  ([07-api-specification.md](07-api-specification.md) §1.3).
- **Acceptance.** `/health` 200 always; `/ready` 503 with a per-dependency breakdown when Postgres is
  down. Generated `/openapi.json` matches [07-api-specification.md](07-api-specification.md).
- **Tests.** `test_health_always_200`, `test_ready_503_when_db_down`.

### `T-2.2` — Inbound JWT validation *(M)* — **BLOCKED ON `Q-01`**

- **Objective.** REQ-AUTH-06.
- **Details.** JWKS fetch and TTL cache with an unknown-`kid` refresh (rate-limited); **RS256 only**;
  `iss`/`exp`/`nbf` with 30 s leeway; `azp`/`aud` against `BACKEND_ALLOWED_CLIENT_IDS`; 401 vs 403
  semantics. No offline fallback, ever.
- **Blocked because** the PDF never states the validation mechanism, the expected audience, or which
  Keycloak client the Backend presents. The validator can be **built and unit-tested against a fake
  realm** while the question is open; only the allowlist value and final confirmation are blocked.
- **Acceptance.** HS256 and `alg: none` tokens are rejected. A valid token from an unlisted client → 403.
- **Tests.** `test_hs256_token_rejected`, `test_alg_none_rejected`,
  `test_valid_token_wrong_client_returns_403`.

### `T-2.3` — Job request DTOs and validation *(M)*

- **Objective.** REQ-JOB-02/03 plus the security controls.
- **Details.** The discriminated union; UUID strictness; `pageNumber` required-XOR-forbidden;
  **`sourceDownloadUrl` https-only with host allowlisting, resolve-then-check, no redirects** (SSRF —
  [12-security.md](12-security.md) §5); path-traversal rejection on `folderId`/`pathPrefix`; 16 KB body
  cap.
- **Acceptance.** Every validation case in [17-testing-strategy.md](17-testing-strategy.md) §3.1 passes.
- **Tests.** `test_page_number_required_for_job_b`, `test_source_url_host_must_be_allowlisted`,
  `test_path_prefix_rejects_traversal`, `test_unknown_jobtype_returns_422`.

### `T-2.4` — Idempotent accept *(M)*

- **Objective.** REQ-IDEM-01/02, REQ-JOB-01/05.
- **Details.** `INSERT INTO jobs … ON CONFLICT (job_id) DO NOTHING` as a **single statement** so two
  concurrent identical POSTs cannot both create work. Canonical `payload_hash` over
  `(jobType, fileId, folderId, pathPrefix, pageNumber)` — **excluding `sourceDownloadUrl`**, because a
  presigned URL legitimately changes between retries (REQ-SRC-02 exists precisely for that). Same hash →
  202 with `duplicate: true`; different hash → 409.
- **Acceptance.** 20 concurrent identical POSTs → one `jobs` row, twenty 202s.
- **Tests.** `test_duplicate_jobid_inserts_once`, `test_concurrent_identical_posts_create_one_job`,
  `test_duplicate_jobid_different_payload_returns_409`,
  `test_same_payload_different_source_url_is_not_a_conflict`.

### `T-2.5` — Worker runtime and queue claim *(M)*

- **Objective.** REQ-JOB-04.
- **Details.** The claim query from [11-data-and-storage.md](11-data-and-storage.md) §3 with
  `FOR UPDATE SKIP LOCKED`; lease heartbeat; bounded job and page concurrency; graceful `SIGTERM`
  handling that stops claiming and drains.
- **Acceptance.** Two workers claim different jobs. An expired lease is re-claimable with `attempt`
  incremented.
- **Tests.** `test_two_workers_claim_different_jobs`, `test_expired_lease_is_reclaimable`.

### `T-2.6` — `GET /api/jobs/{jobId}` (ops) *(S)*

- **Objective.** Answer "which pages have not reported?" in one request.
- **Details.** The shape in [07-api-specification.md](07-api-specification.md) §1.5. No artifact bytes,
  no source URL.
- **Acceptance.** For a job with pages 27–29 non-terminal, `pages.nonTerminal == [27,28,29]`.

---

# Phase 3 — AI integration

Depends on `T-1.3`. Highly parallelisable across adapters.

### `T-3.1` — Streaming page renderer *(M)*

- **Objective.** Render page N without materializing pages 1..N.
- **Affected.** New `adapters/ai/renderer_pdf.py` wrapping `src/pipeline/ocr_gemini.py:56 render_page`
  and/or `walidapro/ocr_verifier/pdf_render.py:41`.
- **Details.** `walidapro/ocr_verifier/pdf_render.py:41-107` currently returns a full
  `list[(page_no, PIL.Image)]` — roughly **14 MB of RGB per page at 220 DPI**, so ~1.4 GB for a 100-page
  book before any work starts. Provide a generator that yields one page at a time and closes its
  handles.
- **Acceptance.** A 100-page PDF renders page 50 with bounded memory.
- **Tests.** `test_streaming_renderer_does_not_materialize_all_pages`.

### `T-3.2` — `page_count()` *(S)*

- **Objective.** REQ-META-01 needs `pagesCount` before any page work.
- **Details.** `len(doc)` is available at `walidapro/ocr_verifier/pdf_render.py:58` and at
  `src/pipeline/run_ocr_gemini.py:38-40` — in both cases it is computed and then **printed or discarded**.
  Expose it as a function. Enforce `MAX_SOURCE_PAGES`.
- **Acceptance.** Sub-millisecond on the sample corpus. A 1204-page PDF → `SOURCE_INVALID`.
- **Tests.** `test_page_count_matches_pypdf`, `test_page_count_ceiling_enforced`.

### `T-3.3` — OCR adapters and unconditional image rendering *(L)*

- **Objective.** REQ-ART-01 and the `OcrEngine` port.
- **Details.** Three adapters — text layer, Gemini, LightOn — behind one port. Model ids from config.
  Explicit timeouts. Status-aware retry (LightOn currently has **no retry at all**,
  `src/pipeline/ocr_lighton.py:66`). **Make image rendering unconditional and engine-independent**:
  `src/pipeline/run_ocr_lighton.py` has no `pages/` mkdir anywhere, so the LightOn path produces no page
  image today and `tools/build_context_review.py:34-37` compensates by borrowing the sibling Gemini
  folder's JPEG. An empty page returns an empty token sequence, **not** an error.
- **Acceptance.** All three adapters produce contiguous ids from `w000`. Every path produces a JPEG.
- **Tests.** `test_every_ocr_adapter_returns_contiguous_ids`,
  `test_ocr_empty_page_returns_empty_sequence_not_error`.

### `T-3.4` — Deterministic tokenizer and the `Text` artifact builder *(M)*

- **Objective.** REQ-ART-02, and Job A → Job B id stability.
- **Details.** One shared, side-effect-free tokenizer used by both jobs, so `p{page:03d}w{index:03d}` is
  reproducible from the text file alone. The builder emits UTF-8 without BOM, one trailing newline, and
  a newline-only file for an empty page. The exact content already exists as a discarded local variable
  at `src/pipeline/run_ocr_gemini.py:65`.
- **Acceptance.** **Byte-identical output across two runs of the same page** — this is what keeps Job B's
  token ids aligned.
- **Tests.** `test_text_artifact_byte_stable_across_runs`, `test_text_artifact_is_utf8_without_bom`,
  `test_empty_page_yields_newline_only`, `test_same_text_same_page_yields_identical_ids`.

### `T-3.5` — Diacritizer, TTS and aligner adapters *(L)*

- **Objective.** The `Diacritizer`, `TtsEngine` and `Aligner` ports.
- **Details.** **Remove the two module-global model singletons** — `src/pipeline/align_whisper.py:16-23`
  and `src/pipeline/tashkel.py:25-33` — in favour of injected, warmed instances; unguarded lazy globals
  double-build under concurrent first touch. Parameterize `device`/`compute_type` (hardcoded at
  `align_whisper.py:22`). Reuse **`src/pipeline/speak.py:99 bind` verbatim** — it is pure,
  engine-agnostic, and already exercised by a second caller with a different token shape
  (`tools/build_review.py:105`). Add an explicit narrator selector so exactly **one** audio artifact is
  produced (`walidapro/run_full_pipeline.py:1057-1130` synthesizes both female and male today — see
  `Q-08b`). Constrain the audio path to WAV, since MP3 is not in the REQ-UPL-04 enum. Keep
  `walidapro/ocr_verifier/tts/pipeline.py:195 _duration_is_sane` — it catches a real observed
  ~224 s-for-50-words degenerate output (`:493-495`).
- **Acceptance.** Two concurrent page tasks share one model instance. The letters-never-change invariant
  holds.
- **Tests.** `test_diacritizer_preserves_consonantal_skeleton`,
  `test_tts_duration_sanity_gate_rejects_degenerate_audio`, `test_bind_is_monotonic_on_golden_pages`.

### `T-3.6` — Content-keyed caches *(M)*

- **Objective.** Make the existing content caches usable across replicas and safe under concurrency.
- **Details.** The multi-layer caches (`walidapro/ocr_verifier/pipeline.py:71-108`, `:164-263`,
  `:265-333`; `tts/pipeline.py:157-181`) are keyed on the **output directory**, so a new scratch dir
  re-pays for everything. Re-key on content. Replace the lock-free whole-file read-modify-write at
  `diacritization/word_diacritics.py:1143`→`:1405`, which loses entries when two workers share a
  directory.
- **Acceptance.** A re-run in a fresh scratch dir hits the cache. Two concurrent writers lose nothing.

### `T-3.7` — The `JSON` timing artifact builder *(M)*

- **Objective.** REQ-ART-04.
- **Details.** Project `tokens[].t`/`t_src` plus `bind_report` into the versioned, self-contained schema
  in [10-data-contracts.md](10-data-contracts.md) §6.2. Exclude `whisper_words` (21% of the source
  record by bytes), `pdf_sha256_16`, `ocr_txt`, `status`, `box_level`. Include
  `provenance.anchor_ratio`.
- **Acceptance.** Schema-valid; monotonic non-overlapping; all `t` within `[0, duration_ms]`; `t_src`
  present iff `t` is.
- **Tests.** `test_timing_json_matches_schema`, `test_timing_monotonic_and_non_overlapping`,
  `test_missing_t_omits_t_src`, `test_zero_anchors_still_produces_valid_timing_json`.

### `T-3.8` — Verified-tier adapter (deferred but scaffolded) *(L)*

- **Objective.** `PageVerifier` exists so enabling the tier is configuration, not architecture.
- **Details.** Wrap `walidapro/ocr_verifier/pipeline.py:863 process_page` and
  `final_context_reviewer.py:749`. Discard `run_pdf:1796` and `save_outputs:1873`. **Fix the
  double-retry amplification** (`final_context_reviewer.py:807`/`:251` app-3× against the SDK's
  un-overridden `max_retries=2` = up to 9 billed completions) and **add a timeout** (`:825` passes none,
  so the SDK's 600 s read default applies). **Distinguish outage from ambiguity** instead of the blanket
  swallow at `word_diacritics.py:1296`. Set `ENABLE_VERIFIED_TIER=false`.
- **Acceptance.** The tier runs behind the flag; the promotion criteria in
  [ADR-009](19-adrs.md) are documented as unmet.

---

# Phase 4 — Data and storage

Depends on `T-1.3`. `T-4.1` gates Phase 2.

### `T-4.1` — Schema, migrations, `JobStore` *(M)*

- **Objective.** REQ-JOB-05, REQ-IDEM-02.
- **Details.** The full DDL from [11-data-and-storage.md](11-data-and-storage.md) §3 including every
  `CHECK` and every partial index. Alembic. A least-privilege runtime DB user (no `CREATE`) and a
  separate migrator user.
- **Acceptance.** Migrations run clean up and down. The DB enums match `domain/` exactly.

### `T-4.2` — Page ledger and fan-out *(M)*

- **Objective.** INVARIANT-1's foundation.
- **Details.** Create `job_pages` rows `1..pages_count` in one statement from `generate_series`,
  **transactionally, before any page work starts** — so no page can be forgotten by omission. Page claim
  and lease.
- **Acceptance.** `pages_count = 30` → exactly 30 rows, all `PENDING`, all created before the first page
  upload.
- **Tests.** `test_fanout_creates_exactly_pages_count_rows`,
  `test_page_rows_created_before_any_page_work`.

### `T-4.3` — Artifact ledger *(S)*

- **Objective.** Upload idempotency.
- **Details.** Row written **only after** `confirm-upload` returns 200, with
  `(job_id, page_number, artifact_type)` as the primary key so a retried page skips what already
  confirmed.
- **Tests.** `test_confirmed_artifact_not_reuploaded_on_retry`,
  `test_artifact_row_written_only_after_confirm_upload`.

### `T-4.4` — Callback outbox and drainer *(M)*

- **Objective.** Callbacks survive worker death.
- **Details.** Row written in the **same transaction** as the state change that causes it. The
  `one_callback_per_target` unique constraint makes a double `completed` for one page structurally
  impossible. Drainer with exponential backoff and full jitter, `CALLBACK_MAX_ATTEMPTS`, then park and
  alert.
- **Acceptance.** Killing the worker between upload and callback loses nothing.
- **Tests.** `test_callback_row_written_in_same_transaction_as_state_change`,
  `test_delivered_row_never_resent`, `test_unique_constraint_blocks_double_completed`.

### `T-4.5` — Reconciler *(M)*

- **Objective.** The INVARIANT-1 backstop.
- **Details.** The sweep in [09-async-jobs.md](09-async-jobs.md) §7.4: force-fail non-terminal pages past
  deadline; escalate to `PATCH /api/jobs/{jobId}/failed` for jobs that never reached `METADATA_SENT`;
  delete orphaned scratch. Advisory lock so running two is harmless. Increment
  `ai_engine_pages_total{outcome="force_failed"}`, **which must be zero in normal operation**.
- **Acceptance.** A `RUNNING` page abandoned past deadline gets exactly one `/failed`.
- **Tests.** `test_stale_nonterminal_page_force_failed`, `test_reconciler_is_idempotent`,
  `test_job_without_metadata_escalates_to_job_failed`.

### `T-4.6` — Scratch workspace lifecycle and retention purge *(S)*

- **Details.** Per-job UUID-named directory — **not** the PDF filename stem, whose real values contain
  Arabic text, spaces, `?` and `' - Copy'` (`src/pipeline/main.py:62-63`). Deletion on every terminal
  path. Atomic writes via the pattern already at `walidapro/ocr_verifier/utils.py:48`. Terminal-job purge
  after `RETENTION_DAYS_TERMINAL_JOBS`.
- **Tests.** `test_scratch_deleted_on_every_terminal_path`,
  `test_terminal_jobs_purged_after_retention_days`.

---

# Phase 5 — External integrations

Depends on `T-1.2`, `T-1.4`. `T-5.1` gates the rest.

### `T-5.1` — Keycloak token provider *(M)*

- **Objective.** REQ-AUTH-01/02/03/04.
- **Details.** `client_credentials`; in-memory cache only; refresh below 30 s of the 300 s life;
  **single-flight** so N concurrent page tasks cause one request; one forced-refresh-and-retry on 401;
  **never retry a 401 from Keycloak itself** (bad credentials must page a human, not back off).
  Never log the token.
- **Acceptance.** 20 concurrent tasks at expiry → exactly one token request.
- **Tests.** `test_token_reused_above_30s_remaining`, `test_token_refreshed_below_30s_remaining`,
  `test_token_refresh_is_single_flight`, `test_token_never_persisted`.

### `T-5.2` — Backend HTTP client and shared retry policy *(M)*

- **Objective.** REQ-AUTH-05 plus one correct retry policy everywhere.
- **Details.** `httpx` client with per-operation timeouts from
  [14-error-model.md](14-error-model.md) §7; the classification table from §5; exponential backoff with
  **full jitter**; `Retry-After` honoured. This replaces three existing anti-patterns:
  `walidapro/ocr_verifier/lighton_ocr.py:180` retries auth failures like 429s, `:197` has no jitter
  (worst case 546 s of blocked worker), and `final_context_reviewer.py` stacks app retries on SDK
  retries. **Set every provider SDK's retry count explicitly**, usually to 0.
- **Tests.** `test_401_retried_once_after_forced_refresh`, `test_403_and_404_never_retried`,
  `test_retry_after_header_honoured`, `test_backoff_has_full_jitter`.

### `T-5.3` — Source fetcher *(M)*

- **Objective.** REQ-SRC-01/02.
- **Details.** Streamed download with a hard `MAX_SOURCE_BYTES` cap; magic-byte verification; a 403 on a
  presigned URL treated as **expired** → one `get-download-url` refresh → retry, with a loop guard;
  parse failure → `SOURCE_INVALID` → whole-job `/failed`, never a crash. The SSRF guard from `T-2.3`
  applies at fetch time too (resolve-then-check).
- **Tests.** `test_expired_source_url_recovers`, `test_malformed_pdf_fails_whole_job`,
  `test_source_over_size_cap_rejected`.

### `T-5.4` — Metadata callback and the page-work gate *(S)*

- **Objective.** REQ-META-01.
- **Details.** `PATCH /metadata {pagesCount:N}` via the outbox. **No page work starts until
  `jobs.metadata_sent_at IS NOT NULL`** — otherwise the Backend receives page callbacks for a job whose
  page count it does not know. Undeliverable past the cap → whole-job `/failed`.
- **Tests.** `test_metadata_patched_before_any_page_upload`.

### `T-5.5` — Artifact uploader *(M)*

- **Objective.** REQ-UPL-01..05.
- **Details.** The three-step cycle; `requiredHeaders` passed to the PUT **verbatim** (a presigned
  signature covers them, so no re-casing or reordering); `sizeInBytes` exactly matching the bytes; the
  four permitted content types only; mandated ordering image→text and audio→json; a 403 on the PUT
  treated as an expired ticket → one fresh `initialize-upload`.
- **Tests.** `test_upload_order_image_then_text`, `test_upload_order_audio_then_json`,
  `test_content_type_is_one_of_four_allowed`, `test_required_headers_passed_verbatim`.

### `T-5.6` — Page and job callbacks *(M)*

- **Objective.** REQ-CB-01..05, REQ-ERR-01.
- **Details.** `completed` only after both `artifacts` rows exist; exact `artifactType` strings;
  `/failed` with a bounded, redacted reason; whole-job `/failed`. All via the outbox.
- **Tests.** `test_completed_sent_only_after_both_confirm_uploads`,
  `test_artifact_type_values_are_exact`.

### `T-5.7` — Fleet-wide provider rate budget *(M)*

- **Objective.** Do not walk into a quota wall. Not hypothetical: `README.md:491` records a real
  free-tier cap of **10 requests/day per model**, and Hume quota exhausted mid-book.
- **Details.** A Postgres-backed counter per provider per window, checked before a paid call. On
  exhaustion, **park the page rather than burning attempts**.
- **Tests.** `test_provider_budget_enforced_fleet_wide`.

---

# Phase 6 — Testing

### `T-6.1` — The contract fake *(L)* — **highest-leverage task in the plan**

- **Objective.** Develop and test the whole V1 protocol without the Backend team's availability.
- **Details.** A small FastAPI app implementing all five Backend endpoints, a presigned-upload
  stand-in, and Keycloak `/token` + `/certs`. Injectable faults: 401, 403-as-expiry, 5xx, 429 with and
  without `Retry-After`, slow responses, duplicate `confirm-upload`, duplicate `completed`, expiring
  upload tickets. **Records and asserts call order** — four of the twenty critical tests are ordering
  assertions.
- **Acceptance.** The full Job A and Job B happy paths run end to end against it, offline.

### `T-6.2` — INVARIANT-1 test suite *(M)*

- **Details.** `test_page_3_fails_page_4_times_out_all_pages_still_report`;
  `test_worker_sigkill_mid_page_recovers`; `test_100_page_job_completes_all_callbacks`.
- **Acceptance.** In every scenario, exactly one terminal callback per page in `1..N`.

### `T-6.3` — Auth and token test suite *(S)*

- **Details.** The `REQ-AUTH` cases, plus `test_hs256_token_rejected` and `test_alg_none_rejected` as
  standing regression tests against algorithm confusion.

### `T-6.4` — Golden fixtures and AI quality tests *(M)*

- **Details.** Copy real artifacts from `work/` into `tests/golden/` — `work/فلفل المغرور-lighton/p003.json`
  and `work/النجوم/p003.json` cover both the box-bearing and box-free variants and both TTS engines.
  Keep Arabic filenames and content deliberately: the encoding bugs in this system will be Arabic
  encoding bugs. Add `test_anchor_ratio_on_golden_corpus_not_below_baseline` against the measured **0.78**
  corpus baseline.

### `T-6.5` — Artifact contract tests *(S)*

- **Details.** `test_text_artifact_byte_stable_across_runs` and the timing-schema suite. Byte stability
  matters because Job B re-tokenizes the text file.

### `T-6.6` — CI *(M)*

- **Details.** There is **no CI today at all**. Add the gate set from
  [17-testing-strategy.md](17-testing-strategy.md) §6: ruff, `lint-imports`, mypy, `pytest tests/
  src/tests/`, coverage, e2e against the fake. Nightly: performance guards and the opt-in real-provider
  contract check.
- **Acceptance.** A PR that violates a layering rule or breaks one of the 56 existing tests fails.

---

# Phase 7 — Deployment

### `T-7.1` — Dockerfile *(M)*

- **Details.** Multi-stage; one image, three entrypoints; non-root user; read-only rootfs;
  `.dockerignore` excluding `work/`, `assets/PDF Samples` (780 MB), `tmp/` (115 MB) and `SESSION.md`
  (740 KB) — otherwise the build context is gigabytes.
- **Acceptance.** Image builds; all three entrypoints run; `docker history` shows no secret.

### `T-7.2` — Compose stack *(S)*

- **Details.** The file in [18-deployment.md](18-deployment.md) §3, including `depends_on` conditions,
  the migrate one-shot, and both volumes.
- **Acceptance.** `docker compose up` reaches all-ready from a cold start.

### `T-7.3` — Model weights in the image or a pinned cache *(M)*

- **Objective.** Deterministic startup and a pinned model.
- **Details.** Pre-bake faster-whisper `small` (464 MB) at build time, or pin it by revision in the
  cache volume. Without this, a cold container downloads 464 MB before it can serve — and the model can
  silently change between deploys, which is a reproducibility and supply-chain problem as much as a
  latency one.
- **Acceptance.** A cold container reaches `/ready` with no network access to HuggingFace.

### `T-7.4` — Graceful shutdown *(S)*

- **Details.** On `SIGTERM`: stop claiming, let in-flight page tasks finish, drain the outbox once, exit.
  `stop_grace_period: 120s`. **A hard kill must remain safe** — correctness comes from the lease and the
  reconciler, not from cooperative shutdown.
- **Tests.** `test_sigterm_drains_then_exits`, `test_sigkill_is_recovered_by_lease_expiry`.

### `T-7.5` — Pre-flight and runbook *(S)*

- **Details.** Execute the checklist in [18-deployment.md](18-deployment.md) §9 with the Backend team,
  including the network reachability matrix and the smoke sequence.

---

# Phase 8 — Production hardening

| Task | Objective | Effort |
|---|---|---|
| `T-8.1` | Parallelize the per-word diacritics loop (`walidapro/.../word_diacritics.py:1611-1660`) — a **precondition** for the verified tier, since p90 is 69 sequential VLM round trips per page | L |
| `T-8.2` | A `walidapro/` test suite — 16,155 LOC currently has zero tests and is not collectible. The other precondition for the verified tier. | L |
| `T-8.3` | Cost dashboards and per-job budgets from `ai_engine_inference_calls_total` | M |
| `T-8.4` | OpenTelemetry tracing (`job` → `page` → `stage`) | M |
| `T-8.5` | `pip-audit`, SBOM, image signing, base-image scanning | M |
| `T-8.6` | Evaluate a compressed audio codec pending `Q-09` — WAV is ~1.0 MB/page median, i.e. 36 GB vs 12 GB at 1,000 books per `README.md:186-190` | S |
| `T-8.7` | Evaluate Hume native word timestamps as the default aligner — measurably better (**93.7% vs 51.0% anchored** on real books, `README.md:473,491`) but historically quota-limited | M |
| `T-8.8` | Consolidate the two pipelines, once there is a service with observability around the change | L |

---

# Definition of Done for V1

- [ ] All 20 critical tests in [17-testing-strategy.md](17-testing-strategy.md) §4 pass.
- [ ] `pytest src/tests` → 56 passed, in CI, on every commit.
- [ ] A real book runs end to end against the real Backend: `metadata` → N × (image, text, `completed`).
- [ ] A Job B runs end to end: audio → json → `completed`.
- [ ] A duplicate `jobId` returns 202 and does not re-execute.
- [ ] A corrupt PDF produces a whole-job `/failed` and **no page callbacks**.
- [ ] A page failure and a page timeout in the same job both still report, and the job reaches
      `COMPLETED`.
- [ ] `ai_engine_outbox_pending == 0` and `ai_engine_pages_total{outcome="force_failed"} == 0` after a
      full run.
- [ ] Every latency figure in this document set has been replaced by a measured value from `T-0.3`.
- [ ] `Q-01`, `Q-02`, `Q-03`, `Q-04`, `Q-05` answered and the implementation matches the answers.
- [ ] No secret in the repository; all eight keys rotated; the three dead keys revoked.
- [ ] `docker compose up` from cold reaches all-ready with no network access to HuggingFace.

# Deliberately deferred past V1

| Deferred | Trigger to revisit |
|---|---|
| The verified tier as the default | All four [ADR-009](19-adrs.md) promotion criteria met |
| Consolidating the two pipelines | After V1 ships and `T-8.2` gives `walidapro/` a test suite |
| The human-in-the-loop correction path (`tools/context_detector.py` + `apply_context_suggestions.py`) | A product decision to add a human review step; it is **human-gated by design** and cannot be automated |
| Bounding boxes as an artifact | `Q-10` — the reader needs image-view highlighting, and the V1 contract has **no `artifactType` slot for boxes at all** |
| A message broker | Sustained enqueue > ~50 jobs/s, or a need for priority classes ([ADR-004](19-adrs.md)) |
| Distributed tracing | Page latency becomes the dominant problem |
| Inbound rate limiting | A second consumer appears, or the Backend's retry storms become harmful |
| GPU inference | Any stage moves to a GPU model ([ADR-003](19-adrs.md)) |
| A compressed audio codec | `Q-09` answered |
| mTLS between the services | `Q-15` answered |
