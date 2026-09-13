# Service Boundaries

## 1. Classification

**V1 has exactly one new network API surface.** Everything else is a process role, an internal module,
or an external dependency. This is deliberate and is enforced by the decision matrix in
[08-ai-integration.md](08-ai-integration.md).

| Component | Classification |
|---|---|
| Job Intake API | **API process** (the only HTTP server we own) |
| Job Orchestrator / Worker Runtime | **Worker process** |
| Reconciler | **Worker process** (a scheduled mode of the worker image) |
| Page Task Executor | Internal module |
| Extraction Pipeline Adapter | Internal module |
| Audio & Timing Pipeline Adapter | Internal module |
| Verification Tier Adapter | Internal module (opt-in, off by default) |
| Source Acquisition | Internal module |
| Artifact Upload | Internal module |
| Backend Callbacks | Internal module |
| Keycloak Token Provider | Internal module |
| Job Store & Page Ledger | Internal module (backed by Postgres) |
| Observability | Internal module |
| Postgres | External dependency (ours) |
| Backend book APIs, S3, Keycloak | External dependency (theirs) |
| Gemini, LightOn | External dependency (vendor) |

A component earns "service" status here only if it is separately deployed and scaled. Two things qualify:
the API and the worker. Nothing else.

---

## 2. Job Intake API

**Responsibility.** Terminate the inbound HTTP contract: authenticate, validate, deduplicate, persist,
return 202. Nothing else.

**Why it exists.** REQ-JOB-01 mandates an HTTP surface returning 202, and REQ-JOB-04 mandates that the
work happen afterwards. It is a separate process from the worker because it must restart in a second and
must never be blocked by a 460-second provider retry.

| | |
|---|---|
| **Inputs** | `POST /api/jobs` with a Bearer token and one of the two job bodies (REQ-JOB-02/03) |
| **Outputs** | `202 Accepted`; a `jobs` row; a queue entry (the same row) |
| **Dependencies** | Postgres; the JWKS endpoint for token validation |
| **Database/storage** | Writes `jobs` only. No filesystem access at all. |
| **AI dependencies** | **None.** It must not import any adapter, provider SDK, `src.pipeline` or `walidapro`. |
| **External integrations** | Keycloak JWKS (read-only, cached) |
| **Sync/Async** | Synchronous request handling; all work deferred |
| **Scalability** | Stateless. Horizontal, behind a load balancer. 1–2 replicas suffice for V1 — it is not on any bottleneck path (see [05-target-architecture.md](05-target-architecture.md) §7). |
| **Failure behavior** | If Postgres is unreachable it returns `503 EXTERNAL_SERVICE_ERROR` and **does not** return 202. Returning 202 without persisting would be a lie: the Backend would stop retrying a job that will never run. This is the correct trade and is challenged in [C-10](24-architecture-challenge.md). |
| **Authentication** | Inbound Keycloak JWT (REQ-AUTH-06). Mechanism is `Q-01`; PROPOSAL in [12-security.md](12-security.md). |
| **Observability** | `request_id` per request; `ai_engine_jobs_accepted_total{job_type,outcome}` with `outcome ∈ {new, duplicate, rejected}`; audit log line per accept |
| **Deployment** | Container, port 9099, `/health` + `/ready`, no volumes, no model cache |
| **API exposure** | `POST /api/jobs` (**REQUIREMENT**); `GET /health`, `/ready`, `/metrics` and optionally `GET /api/jobs/{jobId}` (**PROPOSAL**, ops only) |

---

## 3. Job Orchestrator / Worker Runtime

**Responsibility.** Claim queued jobs, drive them to a terminal state, and guarantee INVARIANT-1.

**Why it exists.** The AI work is blocking, CPU-heavy and long: forced alignment alone is **5.42 s of CPU
per 27.5 s of audio (measured)** while holding **464 MB (measured)** of weights, and every provider call
in the existing code is a synchronous `requests.post` with `time.sleep` retries
(`walidapro/ocr_verifier/lighton_ocr.py:197`). None of that can live in the request path.

| | |
|---|---|
| **Inputs** | Claimed `jobs` rows |
| **Outputs** | Uploaded artifacts; outbox entries; job/page state transitions |
| **Dependencies** | Postgres; scratch volume; model cache volume; all internal modules |
| **Database/storage** | Reads/writes `jobs`, `job_pages`, `artifacts`, `callback_outbox`; owns `scratch/{job_id}/` |
| **AI dependencies** | All of them, via ports |
| **External integrations** | Backend, S3, Keycloak, Gemini, LightOn |
| **Sync/Async** | Async at the job and page level (bounded concurrency); each page task is internally sequential because the stages share a scratch directory and feed each other |
| **Scalability** | Horizontal by replica. Per-job page concurrency AND a global provider-rate budget bound the fan-out — the first bottleneck is provider quota, not CPU (`README.md:491` records a real 10-req/day free-tier wall). |
| **Failure behavior** | Crash-safe: a claim has a lease, so an abandoned job is re-claimable. Page failures are isolated and reported. Job-level failure escalates to `PATCH /api/jobs/{jobId}/failed`. |
| **Authentication** | No inbound surface. Outbound: Backend token via the Token Provider; vendor API keys from config. |
| **Observability** | `job_id` on every log line; `ai_engine_job_duration_seconds`, `ai_engine_queue_depth`, `ai_engine_queue_oldest_age_seconds` |
| **Deployment** | Same image as the API, different entrypoint. Needs both volumes. Readiness waits for model warm-up. |
| **API exposure** | **None.** It serves no HTTP. |

---

## 4. Reconciler

**Responsibility.** Find jobs that will otherwise stall the Backend forever, and force them terminal.

**Why it exists.** Purely because of REQ-META-02: *"the backend will consider that you finish your
process after you completed the 30 pages whether they fail or succeed, if 29, no, the backend will wait
for number 30 page."* A worker that is SIGKILLed between "page 7 uploaded" and "page 7 reported" leaves
the book permanently incomplete. No amount of in-process care fixes that; only an external sweep does.
This component is traceable to a requirement, not a preference — see
[20-traceability-matrix.md](20-traceability-matrix.md) §reverse.

| | |
|---|---|
| **Inputs** | Timer tick; `jobs` and `job_pages` state |
| **Outputs** | Forced `/failed` page callbacks; job `FAILED`/`DEAD_LETTER` transitions; scratch cleanup |
| **Dependencies** | Postgres; `JobCallbacks` |
| **Database/storage** | `jobs`, `job_pages`, `callback_outbox`; deletes orphaned scratch directories |
| **AI dependencies** | None |
| **Sync/Async** | Scheduled, periodic (PROPOSAL: every 60 s) |
| **Scalability** | Single active instance is sufficient and simplest; it takes an advisory lock so running two is harmless |
| **Failure behavior** | Idempotent by construction — it only ever moves a non-terminal page to terminal. Re-running it changes nothing. |
| **Authentication** | Outbound Backend token |
| **Observability** | `ai_engine_pages_total{outcome="force_failed"}` — **this metric should normally be zero; any non-zero value is an alert**, because it means a worker died mid-page |
| **Deployment** | Worker image, `--mode reconcile`, or an in-worker scheduled task |
| **API exposure** | None |

---

## 5. Page Task Executor

**Responsibility.** Take one `(job, pageNumber)` and drive it to exactly one terminal callback.

**Why it exists.** It is the unit that INVARIANT-1 is defined over. It exists as a distinct module — not
inlined into the worker loop — so that failure isolation, deadline enforcement and the "exactly one
terminal report" guarantee live in one auditable place. The existing code's equivalent logic is spread
across seven drivers, each of which gets it wrong the same way (`break`/`return` on error).

| | |
|---|---|
| **Inputs** | `PageTaskSpec` (job id, page number, job type, quality tier, scratch dir) |
| **Outputs** | Uploaded artifacts + one outbox entry (`completed` or `failed`) |
| **Dependencies** | The pipeline adapters, `ArtifactUploader`, `JobCallbacks`, page ledger |
| **Database/storage** | `job_pages`, `artifacts`; per-page scratch subdirectory |
| **Sync/Async** | Runs inside a worker's concurrency slot; internally sequential |
| **Failure behavior** | Wraps everything. Any exception → classify → error code → bounded safe reason → `/failed`. A wall-clock deadline (`PAGE_TASK_TIMEOUT_SECONDS`, PROPOSAL 900 s) converts a hang into `PROCESSING_TIMEOUT` rather than an indefinite hold. |
| **Observability** | `ai_engine_page_duration_seconds{job_type,stage}`; `page_number` on every log line |
| **API exposure** | None |

---

## 6. Extraction Pipeline Adapter (Job A)

**Responsibility.** For one page: render an image, extract text, diacritize, and hand back two artifact
byte streams.

**Why it exists.** To hold the seam between the service and 3,385 LOC of existing pipeline code, so the
service never imports `src.pipeline` directly and the pipeline never learns about jobs or HTTP.

| | |
|---|---|
| **Inputs** | Local PDF path, page number, quality tier |
| **Outputs** | `ExtractionResult` — JPEG bytes, `text/plain` bytes, token list, provenance |
| **Dependencies** | `PageRenderer`, `OcrEngine`, `Diacritizer` ports |
| **Wraps** | `src/pipeline/extract.py:85`, `ocr_gemini.py:65`, `ocr_lighton.py:65`, `tashkel.py:35`, and the render at `ocr_gemini.py:56` |
| **AI dependencies** | Gemini `gemini-3.6-flash` or LightOn `parse-v3` (OCR path only); the local CPU BiLSTM diacritizer |
| **Sync/Async** | Blocking; runs in a worker slot |
| **Failure behavior** | Raises a typed `PipelineError` with a stage label. Never `SystemExit`, never a blanket swallow — both are existing anti-patterns (`tools/ensemble_review.py:133`, `walidapro/.../word_diacritics.py:1296`). |
| **Scalability** | Stateless per call once the diacritizer model is warm; the model must be a shared injected instance, not the module global at `src/pipeline/tashkel.py:25-33` |
| **Observability** | `ai_engine_inference_duration_seconds{provider,model,stage}`; token count and `diac_ratio` logged per page |
| **API exposure** | None |

---

## 7. Audio & Timing Pipeline Adapter (Job B)

**Responsibility.** For one page of free text: synthesize audio, recover word timings, bind them to
stable token ids, and hand back two artifact byte streams.

**Why it exists.** This is the only path in the repository that can produce the mandated
`artifactType: "JSON"` timing artifact. A repo-wide search for
`timing|timestamp|word_time|highlight|srt|vtt` across all `walidapro/**/*.py` returns **zero hits**, so
Pipeline W is structurally incapable of satisfying REQ-ART-04. See [ADR-009](19-adrs.md).

| | |
|---|---|
| **Inputs** | Free text bytes, page number |
| **Outputs** | `AudioResult` — WAV bytes (24 kHz mono 16-bit), timing JSON bytes, `bind_report` |
| **Dependencies** | Deterministic tokenizer, `TtsEngine`, `Aligner` ports |
| **Wraps** | `src/pipeline/tts_gemini.py:21`, `tts_hume.py:15`, `align_whisper.py:26`, and **`speak.py:99 bind` verbatim** |
| **AI dependencies** | Gemini `gemini-3.1-flash-tts-preview` (or Hume); local faster-whisper `small` int8 |
| **Sync/Async** | Blocking. TTS and alignment stay in the same task because alignment consumes the audio bytes TTS just produced — separating them means shipping ~1 MB per hop for no benefit. |
| **Failure behavior** | Typed errors per stage. A zero-anchor page still produces valid output with `t_src` absent on untimed tokens; the timing schema defines what a missing `t` means. Degraded, not failed. |
| **Scalability** | CPU-bound at RTF ≈ 0.20 (measured). Embarrassingly parallel across pages; the faster-whisper model must be a shared injected instance, not the module global at `src/pipeline/align_whisper.py:16-23`. |
| **Observability** | `ai_engine_alignment_anchor_ratio` — the quality canary. Corpus baseline **0.78 measured** across 95 pages; per-book it ranges from 51.0% (Gemini+whisper) to 93.7% (Hume native), per `README.md:473,491`. |
| **API exposure** | None |

---

## 8. Verification Tier Adapter (opt-in)

**Responsibility.** Expose Pipeline W's higher-assurance OCR verification, final text audit and
per-word visual diacritics behind a per-job flag.

**Why it exists.** So that 5,539 LOC of genuinely careful correctness machinery is not thrown away —
and is not switched on by accident. It is **off by default** in V1 because its cost is 40–200 paid calls
per page against Pipeline S's 1–2, and because it has zero tests across 16,155 LOC.

| | |
|---|---|
| **Inputs** | Page image + OCR text, quality tier `verified` |
| **Outputs** | Corrected text, per-word diacritics, `review_required` flag, `SuspiciousWord` records |
| **Wraps** | `walidapro/ocr_verifier/pipeline.py:863 process_page`, `reviewer.py`, `visual_reviewer.py`, `final_context_reviewer.py:749`, `diacritization/*` |
| **Sync/Async** | Async only, and **must not be enabled until the per-word diacritics loop is parallelized** (`walidapro/.../word_diacritics.py:1611-1660` is strictly serial at one VLM call per word; measured Arabic words per page: median 35, p90 69, max 137) |
| **Failure behavior** | Must distinguish infrastructure failure from genuine ambiguity. Today it cannot: `word_diacritics.py:1296` blanket-swallows into `REVIEW`, so a provider outage becomes a page of 100% `REVIEW` that still reports success. Fixing that classification is a precondition of enabling the tier. |
| **Preconditions** | `LIGHTON_BASE_URL` must be set correctly — as shipped, `walidapro/ocr_verifier/config.py:12`'s default `http://localhost:8000/v1` plus the client's `/api/v3/parse` suffix builds `http://localhost:8000/v1/api/v3/parse`, and the variable is set in no `.env` in the repo. Also: `REVIEWER_MODEL` has no default and both reviewer classes raise in `__init__` (`reviewer.py:27-31`, `visual_reviewer.py:160-164`). |
| **Safety invariants that must not be weakened** | letters can never change (`diacritization/word_diacritics.py:1275`, `rebuild_text.py:494`, `:536`); the vision model never sees the candidate answer (`visual_reviewer.py:133-150`); an unclear visual read can never authorize a letter change (`decision.py:375-409`) |
| **API exposure** | None |

---

## 9. Source Acquisition

**Responsibility.** Turn a presigned URL into a local file, surviving expiry.

**Why it exists.** REQ-SRC-01/02. Every existing entry point requires a local `.pdf` and calls
`read_bytes()` (`src/pipeline/run_extract.py:37`, `run_ocr_gemini.py:36`, `run_ocr_lighton.py:41`);
`walidapro/run.py:20` even `SystemExit`s on a non-`.pdf` suffix. There is no HTTP fetch of a source
anywhere in the repository.

| | |
|---|---|
| **Inputs** | `sourceDownloadUrl`, `fileId`, expected content type, scratch dir |
| **Outputs** | Local path + verified size and content type |
| **Failure behavior** | Presigned GET failure → one refresh via `GET /api/files/{fileId}/get-download-url` → retry once. Still failing → `SOURCE_UNAVAILABLE` → whole-job `/failed`. Content that is not a parseable PDF → `SOURCE_INVALID` → whole-job `/failed`, never a crash. |
| **Security** | The URL is attacker-influenced input that we fetch server-side, so **host allowlisting is required, not optional** (SSRF). Magic-byte verification, a max byte size and a max page count all live here. See [12-security.md](12-security.md). |
| **Observability** | `ai_engine_external_call_duration_seconds{target="s3",operation="get"}`; never log the URL itself — it is a credential |
| **API exposure** | None |

---

## 10. Artifact Upload

**Responsibility.** Execute REQ-UPL's three-step cycle for one file, idempotently.

**Why it exists.** Nothing in the repository uploads anything; every artifact is written to a local
relative path and left there (`src/pipeline/run_ocr_gemini.py:56,77`, `run_speak_gemini.py:45,58`).

| | |
|---|---|
| **Inputs** | Bytes (or a path), `contentType` ∈ the REQ-UPL-04 enum, `fileName`, `folderId`, `pathPrefix`, plus `(job_id, page_number, artifact_type)` for idempotency |
| **Outputs** | Backend `fileId` |
| **Database/storage** | Writes an `artifacts` row after `confirm-upload` succeeds |
| **Failure behavior** | The three steps are not atomic. The `artifacts` row is written **after** `confirm-upload`, and a `(job_id, page_number, artifact_type)` uniqueness constraint means a retried page skips anything already confirmed. Whether re-running `initialize-upload` for the same logical file orphans a Backend file record is `Q-02` — flagged, not assumed. |
| **Sync/Async** | Blocking; strictly ordered per REQ-UPL-05 (image→text, audio→json) |
| **Observability** | `ai_engine_external_call_duration_seconds{target="backend"|"s3"}` |
| **API exposure** | None |

---

## 11. Backend Callbacks

**Responsibility.** Deliver `metadata`, `completed`, `failed` (page) and `failed` (job) PATCHes
reliably.

**Why it exists.** These four calls are how the Backend learns anything at all, and a lost one stalls a
book forever. Delivery therefore cannot be a bare in-line HTTP call inside a page task — if the process
dies between "artifacts uploaded" and "PATCH sent", the page is silently lost. Hence the outbox.

| | |
|---|---|
| **Inputs** | `CallbackEnvelope` rows from `callback_outbox` |
| **Outputs** | Backend PATCH calls; outbox rows marked delivered |
| **Dependencies** | Postgres, Token Provider, `httpx` |
| **Sync/Async** | Async drainer, at-least-once delivery |
| **Failure behavior** | Retry with exponential backoff + full jitter on 5xx/429/connection errors; one refresh-and-retry on 401; never retry 400/404. After the attempt cap the row is parked and alerted on — this is the one failure the engine cannot paper over, because only the Backend can unblock the book. |
| **Idempotency** | At-least-once means the Backend may see a duplicate `completed`. Whether that is safe is `Q-03`. Until answered, the drainer marks a row delivered on the first 200 and never re-sends a row it has confirmed. |
| **Observability** | `ai_engine_callback_attempts_total{endpoint,outcome}`, `ai_engine_outbox_pending` |
| **API exposure** | None |

---

## 12. Keycloak Token Provider

**Responsibility.** Supply a valid Backend bearer token, cheaply.

**Why it exists.** REQ-AUTH-01/02/03/04. Nothing like it exists — the only `Authorization` headers in
the repository are static vendor API keys baked into a `requests.Session` at construction
(`walidapro/ocr_verifier/lighton_ocr.py:19-21`, `reviewer.py:40-43`, `src/pipeline/ocr_lighton.py:67`).

| | |
|---|---|
| **Inputs** | `token_URL`, `client_id`, `client_secret` |
| **Outputs** | A bearer token string |
| **Database/storage** | **None — in-memory only.** REQ-AUTH-03 forbids persisting it. |
| **Sync/Async** | Called synchronously; refresh is **single-flight** so N concurrent page tasks cause one token request, not N |
| **Failure behavior** | Token endpoint unreachable → `AUTHENTICATION_ERROR`, retryable. Every Backend call becomes impossible, so this is effectively a whole-job stall condition; it must be loud. |
| **Caching rule** | Reuse while more than 30 s of the 300 s life remains (REQ-AUTH-02). Cache is per-process, so each worker replica holds its own. |
| **Observability** | `ai_engine_token_refresh_total{outcome}`. **Never log the token, not even truncated.** |
| **API exposure** | None |

---

## 13. Job Store & Page Ledger

**Responsibility.** Be the single source of truth for what has and has not happened.

**Why it exists.** REQ-JOB-05 and REQ-IDEM-02 mandate a durable store keyed by job GUID. INVARIANT-1
additionally requires per-page truth. Today, job state is inferred from whether a file exists with a
given `status` string (`src/pipeline/run_ocr_gemini.py:50-53`), keyed on a directory named after a PDF
filename stem (`src/pipeline/main.py:62-63`) — real folder names contain Arabic text, spaces, `?` and
`' - Copy'`. That is not addressable, not transactional and not restart-safe.

| | |
|---|---|
| **Inputs** | Domain state transitions |
| **Outputs** | Queue claims; ledger reads for the reconciler and for idempotent retries |
| **Database/storage** | `jobs` (PK `job_id` — this *is* the idempotency mechanism), `job_pages`, `artifacts`, `callback_outbox`. DDL in [11-data-and-storage.md](11-data-and-storage.md). |
| **Sync/Async** | Transactional; the queue claim is `SELECT … FOR UPDATE SKIP LOCKED` |
| **Failure behavior** | Postgres down → the API cannot honestly return 202 (see §2) and workers cannot claim. The whole engine degrades to "the Backend keeps retrying", which is the correct behaviour. |
| **API exposure** | None |

---

## 14. Observability

**Responsibility.** Make the system explicable to someone who was not watching.

**Why it exists.** The repository has bare `print()` calls and **zero** timing instrumentation — no
`perf_counter`, no elapsed logging, no report file with wall-clock numbers anywhere. Every latency figure
in this document set is an estimate as a direct result. Meanwhile the two best quality signals in the
system — `bind_report` (`src/pipeline/run_speak_gemini.py:52-57`) and
`FinalReviewResult.review_required` — are already computed and written to disk, then never aggregated.

| | |
|---|---|
| **Inputs** | Domain events, stage timings, provider outcomes |
| **Outputs** | Structured JSON logs to stdout; a Prometheus `/metrics` endpoint |
| **Sync/Async** | In-process, non-blocking |
| **Failure behavior** | Must never fail a job. A metrics or logging error is swallowed and counted. |
| **API exposure** | `GET /metrics` on the API role (**PROPOSAL**) |

---

## 15. Boundaries we deliberately did **not** draw

| Tempting boundary | Why we did not draw it |
|---|---|
| OCR as a service | Zero independent callers in V1. It shares a scratch directory with rendering and diacritization. |
| TTS as a service | Same, plus alignment consumes its output bytes directly — a boundary means shipping ~1 MB per page per hop. |
| Alignment as a service | The only argument is the 464 MB resident model, and that is an argument for a *worker*, which we have. |
| Diacritization as a service | Real argument in the verified tier (per-word calls are embarrassingly parallel), but the fix there is intra-process parallelism first. Revisit if it moves to a GPU. |
| A separate upload service | It is 60 lines of HTTP with a uniqueness constraint. |
| A separate scheduler | The reconciler is a timer plus two SQL queries. |
| An artifact store | The Backend owns storage (CONSTRAINT C2). Two copies diverge. [ADR-006](19-adrs.md). |
| A public API gateway | One first-party consumer (CONSTRAINT C1). |
