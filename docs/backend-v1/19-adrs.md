# Architecture Decision Records

Ten decisions. Each records what would reverse it, because a decision without a reversal condition is a
prejudice.

---

## ADR-001 — One job-intake endpoint over REST, discriminated on `jobType`

**Status:** Accepted.

**Context.** The V1 spec mandates `POST {AI_Engine_BASE_URL}/api/jobs` returning `202 Accepted`, with two
`jobType` values carried in the same body shape (REQ-JOB-01/02/03). The Backend is the only consumer
(CONSTRAINT C1). The engine currently has no HTTP surface at all — the only entry points are `argparse`
CLIs (`src/pipeline/main.py:93`, `walidapro/run.py:9`, `walidapro/run_full_pipeline.py:2470`, plus 10 in
`tools/`).

**Decision.** Implement exactly the mandated endpoint, as REST/JSON over HTTPS, with the request body a
pydantic discriminated union on `jobType`. Add `/health`, `/ready` and `/metrics` as operational
endpoints, and an optional `GET /api/jobs/{jobId}` for debugging. **Nothing else is exposed.**

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| A separate endpoint per job type | Cleaner OpenAPI; no discriminator logic; each schema documented independently | The PDF specifies one path. Deviating would require the Backend to change code to talk to us, which is the worst possible first impression for an integration. |
| gRPC | Typed contracts, code generation, better streaming | The PDF specifies HTTP verbs, JSON bodies and status codes. gRPC would need a translation layer, i.e. more work for less conformance. |
| Broker-only ingestion (the Backend publishes to a queue we consume) | Genuinely better back-pressure and no 202-lie problem — the message is durable in the broker, so we never have to persist-then-acknowledge | The PDF's protocol is HTTP with a 202. Also, no shared broker exists between the teams, and introducing one is a cross-team infrastructure project. Revisit for V2. |
| GraphQL | — | There is one operation. It has no query surface. |

**Trade-offs.** A discriminated union means one endpoint handles two moderately different validation
rules (`pageNumber` required-XOR-forbidden). That is a small amount of conditional validation in
exchange for exact spec conformance.

**Consequences.** The OpenAPI document is generated from the same pydantic models that validate
requests, so the contract cannot drift from the implementation. Adding a third job type later is an
additive change to the union. **Reversal condition:** the Backend team asks for per-type paths.

---

## ADR-002 — Modular monolith codebase with separate worker processes; not microservices

**Status:** Accepted.

**Context.** Full option comparison in [05-target-architecture.md](05-target-architecture.md) §2. The
decisive measured facts: forced alignment is **5.42 s of CPU per 27.5 s of audio (RTF ≈ 0.20)** while
holding **464 MB** of weights; every provider call in the existing code is a blocking `requests.post`
with `time.sleep` retries (`walidapro/ocr_verifier/lighton_ocr.py:197`, worst case 546 s); and the AI
stages share a scratch directory by construction — alignment consumes the audio bytes TTS just produced.

**Decision.** One installable package, one container image, three entrypoints (`api`, `worker`,
`reconciler`). The API and the worker are separate processes; everything else is an internal module.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| Single process (FastAPI + background tasks) | Smallest thing that could work; one deploy; trivial local dev; the shared scratch directory comes free. At *books per day*, this is a serious contender. | Blocking multi-minute page work starves the HTTP handler, so `POST /api/jobs` stops answering promptly — REQ-JOB-01 satisfied in code but violated in practice. Any deploy kills in-flight books. API rollouts become 464 MB model-load events. |
| Microservices per AI stage | Stages have genuinely different resource profiles: alignment is CPU-bound and 464 MB resident, OCR/TTS are network-bound and stateless, the verified tier's per-word loop is embarrassingly parallel. Isolates a crash or leak in one stage. | Zero stages are invoked independently in V1. Crossing a network boundary between stages means shipping 1–14 MB per hop. `walidapro/run_full_pipeline.py:503-529` already demonstrates the cost of a mere *process* boundary per page: it reloads the interpreter and the entire PaddleOCR model set every page. Six deploy units against a two-engineer team. |

**Trade-offs.** We accept that a memory leak in one AI stage affects the whole worker, and that scaling
alignment means scaling everything in the worker. Both are acceptable at V1 volumes and both are
observable.

**Consequences.** One test suite, one set of DTOs, one correlation model, one image to build and scan.
**Reversal condition:** a stage needs a GPU; or a second consumer appears wanting one stage in
isolation; or one stage's scale envelope diverges by more than ~10× from the others.

---

## ADR-003 — In-process adapters over remote providers plus CPU-local models; no model server in V1

**Status:** Accepted.

**Context.** Three local models exist, all CPU-pinned by hardcoded choice: PaddleOCR
(`walidapro/ocr_verifier/config.py:32`, `device` defaults to `"cpu"`), faster-whisper
(`src/pipeline/align_whisper.py:22`, `device="cpu", compute_type="int8"`, not parameterized), and the
`arabic-diacritizer` BiLSTM (`src/pipeline/tashkel.py:14-17`, documented CPU-only). The heavy models —
Gemini and LightOn — are remote HTTP APIs.

**Decision.** AI capability is reached through the ports in
[08-ai-integration.md](08-ai-integration.md) §1, implemented by adapters that call remote providers over
HTTP or invoke the local models in-process. **No Triton/TorchServe/vLLM sidecar, no inference service.**

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| A model-serving sidecar (Triton, TorchServe) | Batching, dynamic loading, GPU sharing, model versioning as a first-class concept | There is no GPU and no batchable workload. faster-whisper is called once per page with a page's audio; batching would add latency, not remove it. It would add a deploy unit and an IPC hop to serve one model. |
| A separate inference service per stage | Independent scaling; a crash blast radius of one stage | Same objections as ADR-002's option C. |
| A local vLLM server for LightOn | `walidapro/ocr_verifier/config.py:12`'s default `http://localhost:8000/v1` is a leftover from exactly this setup | Nothing in the repo deploys or pins a vLLM server, and the hosted API is what the parallel implementation actually uses (`src/pipeline/ocr_lighton.py:41` hardcodes `https://api.lighton.ai/api/v3/parse`). Self-hosting is a separate project. |

**Trade-offs.** Model weights live in the worker's memory, so a worker's footprint is ~1 GB rather than
~300 MB, and a cold start pays a model load (measured: 1.54 s warm from disk, 2.56 s including import,
plus a 464 MB download if the cache volume is missing).

**Consequences.** Workers must warm models before readiness, and the model-cache volume becomes a
startup dependency. **Reversal condition:** any stage moves to a GPU model; or the verified tier's
per-word diacritics loop needs to scale to hundreds of concurrent calls independently of page
processing.

---

## ADR-004 — Asynchronous processing with a Postgres-backed queue (`FOR UPDATE SKIP LOCKED`)

**Status:** Accepted.

**Context.** REQ-JOB-04 requires background execution ("A background task will take the enqueued jobs").
REQ-IDEM-02 requires a durable store keyed by job GUID ("verifies it has not any Prev one in db or any
data store via GUID given"). So **a durable store is mandatory regardless of the queue choice** — the
only question is whether to add a second piece of infrastructure for queueing.

**Decision.** Postgres is both the job store and the queue. Workers claim with
`SELECT … FOR UPDATE SKIP LOCKED`. Leases handle worker death.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected for V1 |
|---|---|---|
| Celery + Redis | The default Python answer; mature; good visibility tooling; retries and scheduling built in | A second stateful dependency and a second failure mode, to solve a queueing problem Postgres already handles at book-scale volume. Redis persistence would also have to be configured carefully, since a lost job means a stalled book. |
| RabbitMQ | Real broker semantics, dead-letter exchanges, priorities | Same objection, plus more operational surface. Nothing in V1 needs priorities. |
| SQS / a managed queue | No operational burden; effectively infinite durability | No cloud target is specified anywhere in the PDF, and choosing one would be a requirement-free infrastructure decision. |
| In-process thread pool, no queue at all | Simplest possible | Loses everything on restart; cannot coordinate across replicas; cannot survive a deploy mid-book. |

**Trade-offs.** Postgres-as-a-queue has a real ceiling and real sharp edges: long-running transactions
can bloat, and a very high claim rate causes lock contention. We are nowhere near either — measured
growth is ~128 rows per book ([11-data-and-storage.md](11-data-and-storage.md) §4), and the workload is
books per day. It also means the same database serves the hot claim path and the ledger, which is fine
at this size and would not be at 1000×.

**Consequences.** One stateful dependency to operate, back up and monitor. `SKIP LOCKED` requires
Postgres ≥ 9.5.

**Reversal condition, stated concretely:** sustained enqueue rate above ~50 jobs/second, or a need for
priority classes, or claim-query p99 latency above 50 ms. At that point the `JobStore` port already
isolates the change to one adapter. This is challenged directly in
[C-24](24-architecture-challenge.md).

---

## ADR-005 — Postgres as the single stateful dependency and the idempotency store

**Status:** Accepted.

**Context.** We need transactional multi-row state: a job, its N page rows, its artifact rows and its
outbox rows must move together. INVARIANT-1 depends on being able to ask "are all N pages terminal?"
atomically and correctly.

**Decision.** One Postgres instance. `jobs.job_id` is the PRIMARY KEY and *is* the idempotency mechanism
(REQ-IDEM-01). Four tables, DDL in [11-data-and-storage.md](11-data-and-storage.md) §3.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| SQLite | Zero operational cost; genuinely adequate for one worker; the whole dataset is megabytes | Multiple worker replicas need concurrent writers. `SKIP LOCKED` does not exist. It would force a single-worker design, which ADR-002 rejects. |
| Redis only | Fast; the queue and the store in one | Not a transactional store. INVARIANT-1's "all N pages terminal" check across four related row sets is exactly what Redis is bad at, and getting it wrong strands books. |
| MongoDB | Flexible documents; a job with its pages as a subdocument is a natural fit | No relational integrity, weaker constraint expression, and we specifically *want* constraints — `one_callback_per_target` and `(job_id, page_number, artifact_type)` uniqueness are load-bearing correctness mechanisms, not conveniences. |
| No database — filesystem state | It is what the code does today: page `status` fields in `work/<book>/pNNN.json` (`src/pipeline/run_ocr_gemini.py:50-53`) | Keyed on a PDF filename stem (`src/pipeline/main.py:62-63`) whose real values contain Arabic text, spaces, `?` and `' - Copy'`. Not addressable by GUID, not transactional, not shareable across replicas, no locking. It is precisely what REQ-IDEM-02 cannot be built on. |

**Trade-offs.** Postgres is a single point of failure for the whole engine: if it is down, the API cannot
honestly accept jobs and workers cannot claim. We accept this because the alternative — accepting jobs we
cannot persist — is worse. Faced squarely in [C-10](24-architecture-challenge.md).

**Consequences.** Backups and PITR matter. Migrations are a deploy step. **Reversal condition:** none
foreseeable at V1 scale; the growth figures say megabytes per year.

---

## ADR-006 — No engine-owned artifact store; artifacts go to the Backend's storage

**Status:** Accepted.

**Context.** REQ-UPL's entire three-step cycle exists to put files into the Backend's storage, and the
Backend already owns the bucket, the file records, the lifecycle policy and presumably a CDN
(CONSTRAINT C2). Today the engine writes everything to a local `work/<book>/` tree and leaves it there
(`src/pipeline/run_ocr_gemini.py:56,77`, `run_speak_gemini.py:45,58`).

**Decision.** The engine keeps **no durable artifact store**. It uploads via the mandated cycle, records
only the returned `fileId` in the `artifacts` table, and treats local disk as scratch that is deleted on
every terminal path.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| An engine-owned S3 bucket alongside | Cheap re-upload after a Backend-side loss; artifact-level regression testing; a debugging copy | Two copies of the same artifact with two lifecycles diverge, and it is unclear which is canonical. It also adds a bucket to secure, pay for, and reason about under any future data-deletion request. |
| Shared NFS/EFS between the teams | No upload cycle needed at all | The PDF does not offer it, and it would couple the two deployments at the filesystem level. |
| Keep the existing `work/<book>/` layout as the store | It exists and works | Keyed on a PDF filename stem; not addressable by job; unbounded growth; and it is the source of the concurrency corruption described in [04-gap-analysis.md](04-gap-analysis.md) §3. |

**Trade-offs, stated plainly.** We **cannot re-serve an artifact.** If the Backend loses a file we must
re-run the job and re-pay for the AI. We also cannot diff a re-run against the previous output without
downloading from the Backend, which makes artifact-level regression testing harder.

**Consequences.** Workers are stateless in the dimension that matters. **Reversal condition:**
artifact-level regression testing becomes a requirement, or the Backend asks us to retain a copy.

---

## ADR-007 — Keycloak JWT validation inbound; `client_credentials` with an in-memory single-flight cache outbound

**Status:** Accepted, **with a blocking dependency.**

**Context.** The PDF gives `client_id`/`client_secret` for calling the Backend and specifies the
`client_credentials` flow, the 300 s `expires_in`, the in-memory cache, the 30-second refresh margin and
the 401 semantics (REQ-AUTH-01..05). It says inbound calls carry
`Authorization: Bearer {ai-engine-bearer-token}` (REQ-AUTH-06) and **says nothing about how to validate
it.** Nothing resembling either mechanism exists in the repository — the only `Authorization` headers are
static vendor API keys baked into a `requests.Session` at construction
(`walidapro/ocr_verifier/lighton_ocr.py:19-21`, `reviewer.py:40-43`, `src/pipeline/ocr_lighton.py:67`).

**Decision.**

- **Outbound:** exactly as specified, plus **single-flight refresh** so N concurrent page tasks cause one
  token request rather than N, and one forced-refresh-and-retry on a 401.
- **Inbound (PROPOSAL, pending `Q-01`):** validate as a Keycloak-issued JWT — RS256 only, JWKS fetched
  from the realm's `/certs` and cached with a TTL, `iss`/`exp`/`nbf` checked with 30 s leeway, and
  `azp`/`aud` matched against `BACKEND_ALLOWED_CLIENT_IDS`. `401` for "who are you", `403` for "not
  you".

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| A shared static API key | Trivial; no Keycloak dependency on the inbound path; works offline | Symmetric secret shared between two services: either can impersonate the other, rotation needs a synchronized deploy, and it discards the realm the organization already runs. |
| **Reusing our own `client_secret` to validate inbound tokens** | It is the credential we were given | **A category error.** It is an *outbound* credential. Treating it as an inbound shared secret has every problem of the row above, plus it conflates two independent trust directions. Explicitly rejected so nobody proposes it as a shortcut. |
| mTLS | Strong, no token handling at all | Requires certificate infrastructure and a Backend-side change. Good future hardening (`Q-15`). |
| Passing the Backend's user token through | Would carry the end user's identity | The Backend calls us machine-to-machine; there is no user in the loop. |

**Trade-offs.** Inbound validation depends on Keycloak availability for JWKS. Mitigated by caching with a
TTL and by failing `/ready` rather than crash-looping when Keycloak is briefly unreachable. We
deliberately provide **no offline fallback** — no cached key, no request.

**Consequences.** **`Q-01` blocks `T-2.2`.** Until answered, the JWKS validator can be built and
unit-tested against a fake realm, but the allowlist value is unknown and the mechanism unconfirmed.
**Reversal condition:** the Backend team specifies a different mechanism.

---

## ADR-008 — Model ids as configuration; provenance stamped on artifacts and logs; rollback by config

**Status:** Accepted.

**Context.** Model ids are hardcoded constants throughout: `src/pipeline/ocr_gemini.py:28`,
`tts_gemini.py:16-17`, `tts_hume.py:12`, `tools/context_detector.py:46`,
`walidapro/ocr_verifier/final_context_reviewer.py:246`, `src/pipeline/ocr_lighton.py:42`,
`align_whisper.py:19-23`. Only `walidapro/ocr_verifier/config.py:14,18` is already env-driven. Several of
these are `-preview` models, which by definition will be deprecated.

**Decision.** Every model id, voice and local model size becomes a config key
([15-configuration.md](15-configuration.md) §2.3). Provenance is stamped on every inference log line
(`model`, `model_version`) and inside the timing artifact's `provenance` block. Rollback is a config
change plus a worker restart — no code deploy, no schema change.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| Keep them as constants (status quo) | Simple; the constant is next to the code that uses it | A `-preview` model deprecation would become a code change, a review, a build and a deploy — during an incident. |
| A model registry (MLflow or similar) | Lineage, staged rollout, A/B, metric attachment | Enormously heavier than the problem. There are ~8 model ids, all third-party, none trained by us. |
| Model id per request in the job body | Maximum flexibility | The Backend has no basis for choosing a model, and it would make our output non-reproducible from our own configuration. |

**Trade-offs.** Configuration drift between environments becomes possible. Mitigated by logging the
resolved model id at startup and stamping it on every artifact that has a schema.

**Consequences.** The `Image` and `Audio` artifacts are raw bytes with nowhere to record provenance —
their provenance exists only in our logs and in the sibling timing JSON. Worth knowing before somebody
asks "which TTS model produced this file". We build on the existing convention rather than inventing one:
the page records already carry `ocr_engine`, `tts`, `voice`, `align`, `box_level`.

---

## ADR-009 — Pipeline S is the V1 default for both jobs; Pipeline W is an opt-in verified tier

**Status:** Accepted. **This is the most contestable decision in the document set.**

**Context.** Two independent pipelines exist ([01-current-architecture.md](01-current-architecture.md)
§2). Pipeline W (`walidapro/`) has substantially better correctness machinery. Pipeline S
(`src/pipeline/` + `tools/`) is the maintained core, carries all 56 tests, and has the fast path.

Three facts decide it:

1. **Pipeline W cannot produce the mandated timing artifact.** A repo-wide search for
   `timing|timestamp|word_time|highlight|srt|vtt` across every `walidapro/**/*.py` returns **zero hits**.
   REQ-ART-04 is unsatisfiable on that path. This is not a preference; it is a capability gap.
2. **Cost differs by 40–100×.** Fast is 1–2 paid calls per page. Verified is 65–200, dominated by
   diacritization at **one vision call per Arabic word** — measured distribution over 186 real pages:
   median 35, mean 38.1, p90 69, max 137. Arithmetic in
   [08-ai-integration.md](08-ai-integration.md) §5.
3. **Pipeline W has zero tests across 16,155 LOC** (~83% of production Python) and is not even
   collectible by pytest.

**Decision.** `qualityTier` defaults to `fast` (Pipeline S) for both job types. The verified tier is
reachable per job and gated by an `ENABLE_VERIFIED_TIER` kill-switch that defaults to `false`.
**Nothing in `walidapro/` is deleted** — it is wrapped behind `PageVerifier`.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected for V1 |
|---|---|---|
| **Ship Pipeline W as the default** | It is genuinely more careful. The blind-crop protocol, the letters-never-change guard and the `UnsafeReviewError` envelope are real quality mechanisms that Pipeline S simply lacks. If the product is judged on Arabic OCR accuracy, this is the better engine. | It cannot produce the timing artifact at all, so Job B is impossible on it. And making the first integration simultaneously the most expensive (65–200 calls/page), the slowest (a strictly serial per-word loop, `word_diacritics.py:1611-1660`) and the least tested (zero tests) thing in the system maximizes the chance that the first joint test fails for reasons unrelated to the protocol. |
| Merge the two pipelines first | One codebase, best of both, no tiering | A 16,155-LOC merge with no tests on one side, before any of the integration exists. It would delay V1 by more than it improves it, and it is the kind of work that is much safer to do *after* there is a service with observability around it. |
| Delete Pipeline W | Removes the duplication that is the repo's biggest structural problem | It is the only implementation of several correctness guarantees the product will eventually want. Deleting it destroys real value. |
| Hybrid: W for Job A, S for Job B | Best OCR *and* timings | Job A's `Text` artifact is Job B's input, so the two must agree on tokenization and diacritization. Two different engines producing that boundary is exactly where a subtle mismatch would live. Possible later; not for the first integration. |

**Trade-offs.** V1 ships with measurably weaker OCR correction than the repository is capable of. This is
a real quality reduction and is recorded as `R-11`/`R-12` in
[23-risks-and-tradeoffs.md](23-risks-and-tradeoffs.md). It is the right trade **only if** V1 acceptance
is protocol conformance — which is `Q-07`, and is unanswered.

**Consequences.** The `PageVerifier` port must exist in V1 even though its adapter is off, so that
enabling the tier later is configuration rather than architecture.

**Reversal condition — all four must hold:**

1. `Q-07` is answered as "acceptance is measured on AI accuracy".
2. The per-word diacritics loop is parallelized.
3. A measured cost-per-book figure exists and is approved (today there is **no cost instrumentation at
   all**, and `README.md:190`'s "$38 / 36 books" is a projection for 3-engine consensus OCR, not an
   invoice).
4. `walidapro/` has a test suite.

---

## ADR-010 — Page-level failure isolation, a durable callback outbox, and a reconciler

**Status:** Accepted.

**Context.** REQ-META-02 is unambiguous: *"the backend will consider that you finish your process after
you completed the 30 pages whether they fail or succeed, if 29, no, the backend will wait for number 30
page."* A dropped page callback stalls a book **forever**.

The existing code violates this everywhere. Every page loop aborts all remaining pages on the first
exception (`src/pipeline/run_ocr_gemini.py:59-62` `break`, `run_speak_gemini.py:79-84` `return`,
`run_ocr_lighton.py:58`, `run_speak_hume.py:63-65`), and
`walidapro/ocr_verifier/pipeline.py:1846-1865` buries a page failure as the literal string
`<!-- PAGE FAILED: {exc} -->` inside otherwise-successful output while `run.py` still exits 0.

**Decision.** Four mechanisms, detailed in [09-async-jobs.md](09-async-jobs.md) §7:

1. A `job_pages` row per page, created transactionally at fan-out **before any page work starts**.
2. A `callback_outbox` written in the same transaction as the state change that causes it.
3. Leases, so a dead worker's page becomes re-claimable.
4. A reconciler that force-fails stragglers past deadline and escalates to
   `PATCH /api/jobs/{jobId}/failed`.

**Alternatives considered.**

| Alternative | Its best argument | Why rejected |
|---|---|---|
| Fail the whole job on any page error | Much simpler; one failure path | Contradicts REQ-META-02 directly, which explicitly expects a mixture of failed and succeeded pages. It would also throw away 29 pages of paid work because page 30 hit a 429. |
| Rely on the Backend's retries | The Backend already retries with exponential backoff and dead-letters (REQ-IDEM-03), so a stalled job would eventually be re-delivered | The Backend retries the **job**, not the page. On re-delivery our idempotency check finds the existing `jobId` and returns 202 without doing anything — the classic case where two individually correct mechanisms combine into a permanent stall. This alternative is the most seductive and the most wrong. |
| Best-effort in-line callbacks, no outbox | Less machinery; fewer moving parts | Loses the page permanently if the process dies between the last `confirm-upload` and the PATCH — a window that is seconds long and occurs on every deploy. Worked trace in [09-async-jobs.md](09-async-jobs.md) §7.5. |
| Only the outbox, no reconciler | The outbox already makes the callback durable | The outbox covers "we decided to report and then died". It does not cover "we never got as far as deciding" — a SIGKILL mid-inference leaves a `RUNNING` row with no outbox entry and nothing to drain. Challenged in [C-25](24-architecture-challenge.md). |

**Trade-offs.** Four mechanisms is more machinery than a first version usually carries, and each is a
thing to test and operate. Accepted because the failure it prevents is the single worst outcome in the
system — a silently stalled book, invisible from our side, discovered by the Backend team.

**Consequences.** `ai_engine_pages_total{outcome="force_failed"}` should be **exactly zero** in normal
operation; any non-zero value is an alert, because it means a worker died mid-page. Together with
`ai_engine_outbox_pending == 0`, those two metrics constitute the INVARIANT-1 dashboard.
