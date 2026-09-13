# Risks and Trade-offs

## 1. Risk register

Likelihood and impact are L/M/H. "Early warning" ties to a metric from
[13-observability.md](13-observability.md) §3 wherever one exists — a risk with no detectable signal is
a risk you find out about from the other team.

| ID | Risk | Category | L | I | Early warning | Mitigation | Owner | Residual |
|---|---|---|:-:|:-:|---|---|---|---|
| **R-01** | **INVARIANT-1 violation strands a book in the Backend permanently.** A page that never reports means *"the backend will wait for number 30 page"* forever. | technical | M | **H** | `ai_engine_pages_total{outcome="force_failed"} > 0`; `ai_engine_outbox_pending > 0` for >5 min | Page ledger created before any page work; callback outbox written in the same transaction as the state change; leases; reconciler ([ADR-010](19-adrs.md)) | AI team | **Low.** Four independent mechanisms, and two metrics that are zero in healthy operation. |
| **R-02** | **Cost blowup if the verified tier ships by default.** Diacritization is one vision call **per Arabic word**; measured over 186 real pages: median 35, mean 38.1, p90 69, max 137. Total 65–200 paid calls/page against fast's 1–2. | cost | M | **H** | `ai_engine_inference_calls_total` daily total | `ENABLE_VERIFIED_TIER=false` as a hard kill-switch that downgrades even a job requesting it; fleet-wide provider budget; four documented promotion criteria | AI team | Low, **provided the kill-switch defaults false and is never flipped without `T-8.1` and a cost measurement.** |
| **R-03** | **Timing quality judged inadequate.** 21.8% of tokens are interpolated rather than anchored (measured over 3,873 tokens). Per book it is far worse than the average suggests: `README.md:473,491` records **51.0% anchored + 47.8% interpolated** on the Gemini+whisper book versus **93.7%** on the Hume native-timestamp book. | quality | **H** | M | `ai_engine_alignment_anchor_ratio` fleet p50 | Ship `anchor_ratio` **inside the timing artifact** so the consumer can see it per page; `t_src` per token so the reader can style interpolated words differently; a missing `t` degrades to unhighlightable text rather than breaking (`README.md:473`); `T-8.7` evaluates Hume native timestamps | AI team + product | **Medium — the highest residual risk in the register.** This is a model-capability limit, not a bug: Gemini TTS has no timestamp API at all, so forced alignment is the only option on that path. |
| **R-04** | **Three retrying layers multiply.** The Backend retries the job, we retry the operation, provider SDKs retry the call. Already observed: `walidapro/.../final_context_reviewer.py` bills up to **9 completions for one page** from app-3× (`:807`, `:251`) × SDK-2× (`:267`, never overridden) — and the Backend's retry makes it 27. | cost | M | M | `ai_engine_inference_calls_total` per page vs the expected 1–2 | Set every SDK's retry count explicitly, usually 0; own retries at one layer; prefer idempotency over aggression; never retry a non-retryable status | AI team | Low, once `T-5.2` lands. |
| **R-05** | **Environments irreproducible.** `requirements.txt` declares 1 of 14 real dependencies with **zero** version pins; no packaging, no container, no CI. `pip install -r requirements.txt` then any entry point fails on `from dotenv import load_dotenv`. | operational | **H** | M | CI red on a clean install | `T-0.1`, `T-1.1`, `T-7.1`; a clean-venv install is an acceptance criterion of `T-0.1` | AI team | Low. |
| **R-06** | **Provider quota walls.** Not hypothetical: `README.md:491` records "Gemini free-tier capped at 10 req/day per model" and Hume quota exhausted mid-book, leaving `work/المومياوات ناشيونال جيوجرافيك` with 33 page records and **zero** audio. | integration | **H** | M | `ai_engine_inference_failures_total{error_code="RATE_LIMITED"}`; `ai_engine_queue_oldest_age_seconds` | Fleet-wide budget in Postgres (`T-5.7`); on exhaustion **park the page rather than burning attempts**; honour `Retry-After`; paid plans before go-live | AI team | Medium — depends on a commercial decision outside our control. |
| **R-07** | **`Q-01` unanswered blocks authentication entirely.** | organisational | M | **H** | — | The JWKS validator can be built and unit-tested against a fake realm while the question is open; only the allowlist value is blocked. The contract fake issues its own tokens, so the whole integration can be developed offline. | Backend team | Low, **as long as it is asked now** rather than discovered at the first joint test. |
| **R-08** | **Two unconnected pipelines diverge further.** `walidapro/` arrived as a single commit and references neither `src/` nor `tools/`; the dependency graph is one-way. A bug fixed in one is not fixed in the other. | technical | **H** | M | — (no metric can see this) | V1 wraps both behind the same ports, so at least the *service* has one shape; `T-8.8` consolidates after V1 ships | AI team | **Medium.** Deferred consolidation is the correct sequencing but the divergence keeps accruing. |
| **R-09** | **`walidapro/` has zero tests across 16,155 LOC** (~83% of production Python) and is not even collectible by pytest. | quality | **H** | M | CI coverage | Keep it off the V1 default path ([ADR-009](19-adrs.md)); `T-8.2` before the verified tier is enabled | AI team | Medium while the code remains in the repository. |
| **R-10** | **Postgres is a single point of failure.** If it is down, the API cannot honestly accept jobs and workers cannot claim. | operational | L | **H** | `/ready` failing; API 503 rate | Return `503` rather than a dishonest 202 (the Backend then retries, which is correct); managed Postgres with PITR; the whole dataset is **megabytes** so restore is fast | AI team | Low. Faced directly in [C-10](24-architecture-challenge.md). |
| **R-11** | **The correlated-error correction quality path is simply absent from V1.** `tools/context_detector.py` + `apply_context_suggestions.py` catch errors *both* OCR engines agree on — a class nothing else catches — but they are **human-gated by design**: `context_detector.py:148-160` seeds every decision as `"reject"` and `tools/run_full_pipeline.py:99-115` prints *"STOPPING HERE ON PURPOSE"*. | quality | **H** | M | — | Acknowledged, not mitigated. Automating a deliberately-human step would defeat its purpose. Revisit only as a product decision to add a review stage. | product | **Medium — accepted.** This is a real capability the repository has and V1 does not. |
| **R-12** | **V1 ships measurably weaker OCR correction than the repo is capable of.** Pipeline W's blind-crop protocol, letters-never-change guard and `UnsafeReviewError` envelope have no equivalent in Pipeline S. | quality | M | M | — | [ADR-009](19-adrs.md) with four explicit promotion criteria; the `PageVerifier` port exists in V1 so enabling the tier is configuration, not architecture | product | Medium — **entirely contingent on `Q-07`.** |
| **R-13** | **A long book saturates a worker.** A 200-page book at ~60 s/page serially is over three hours; `JOB_PAGE_CONCURRENCY` bounds its share but a single job can still dominate. | operational | M | M | `ai_engine_queue_oldest_age_seconds`; `ai_engine_queue_depth` | Per-job page concurrency cap; `MAX_SOURCE_PAGES` = 500; horizontal worker scaling | AI team | Low. |
| **R-14** | **Arabic filename and encoding bugs.** Identity today is a PDF filename stem (`src/pipeline/main.py:62-63`) and real directories contain Arabic text, spaces, `?` and `' - Copy'`. | technical | M | M | — | Scratch paths are derived from the `job_id` UUID only — the class of bug is designed out. Fixtures deliberately keep Arabic filenames and content so encoding bugs surface in tests. | AI team | Low. |
| **R-15** | **Orphaned Backend files accumulate.** A page retried after a mid-upload crash mints a second `fileId`; the first is orphaned. Traced in [09-async-jobs.md](09-async-jobs.md) §7.5. | operational | M | L | count of `artifacts` rows vs Backend file count | `Q-02`; every minted `fileId` is logged so orphans are traceable | Backend team | Low — a storage cost, not a correctness problem. |
| **R-16** | **All pages materialized in RAM.** `walidapro/ocr_verifier/pdf_render.py:41-107` returns a full list of PIL images — ~14 MB of RGB per page, so ~1.4 GB for a 100-page book before any work starts. | technical | M | M | worker OOM kills; `ai_engine_scratch_bytes` | `T-3.1` streaming renderer as a hard prerequisite; `MAX_SOURCE_PAGES` | AI team | Low once `T-3.1` lands. |
| **R-17** | **Model weights change under us.** faster-whisper and PaddleOCR weights are fetched from the network at first use, unpinned. | technical | L | M | `ai_engine_alignment_anchor_ratio` shift with no code change | `T-7.3` pre-bakes or pins by revision | AI team | Low. |
| **R-18** | **Secrets already in the repository.** A real `.env` with eight live credentials sits at the repo root, and `load_dotenv()` at import in 11 modules means a mounted `.env` silently overrides injected secrets. | security | **H** | **H** | — | `T-1.2`: delete, `.gitignore`, **rotate all eight keys** (`git rm` does not purge history), revoke the three dead ones, and add a startup assertion that no `.env` is present | AI team | **Low only after rotation. Until then, treat all eight keys as compromised.** |
| **R-19** | **SSRF via `sourceDownloadUrl`.** Caller-supplied and fetched server-side, so anyone who can POST a job can make the worker `GET` an arbitrary URL from inside our network. | security | L | **H** | rejected-host counter | Scheme + host allowlist, resolve-then-check, no redirect following, deny-by-default egress ([12-security.md](12-security.md) §5). **Required for V1, not hardening.** | AI team | Low. |
| **R-20** | **Blanket `except Exception` turns an outage into silent success.** `walidapro/.../word_diacritics.py:1296` converts any failure into `REVIEW`, so a provider outage becomes a page of 100% unshaped text that still reports `completed`. | quality | M | **H** | `ai_engine_diacritization_review_ratio` spike | Distinguish "model uncertain" from "provider broken" ([14-error-model.md](14-error-model.md) §6.1); the review-ratio metric makes it visible | AI team | Medium while `walidapro/` remains untested. |
| **R-21** | **`SystemExit` from library code kills a worker.** 19 sites; `tools/ensemble_review.py:133` raises from inside `run()`. `SystemExit` inherits `BaseException` and escapes `except Exception`. | technical | M | M | worker restart rate | `T-1.5`, plus the `BaseException` catch in the Page Task Executor as a second layer | AI team | Low. |
| **R-22** | **`LIGHTON_BASE_URL` misconfigured as shipped.** The default `http://localhost:8000/v1` plus the client's `/api/v3/parse` suffix builds `http://localhost:8000/v1/api/v3/parse`, and the variable is set in **no** `.env` or script in the repo. | operational | **H** | L | `/ready` or first-page failure | Explicit in `.env.example`; startup validation of every provider base URL | AI team | Low — it fails loudly and immediately. |
| **R-23** | **Non-atomic writes lose work.** Zero matches for `os.replace`/`rename`/`fsync`/`flock` across `src/pipeline` and `tools`. `tools/context_detector.py:17-20` records that `ensemble_review.json` "already nearly lost real `vlm_arbiter.py` work to a careless write once tonight". | technical | M | M | — | Per-job scratch isolation; atomic writes reusing the pattern already at `walidapro/ocr_verifier/utils.py:48` | AI team | Low. |

### The five that matter most

`R-01` (a stranded book — the worst outcome), `R-02` (a 100× cost surprise), `R-03` (the quality
judgement we cannot fully control), `R-05` (irreproducible environments, which blocks everything), and
`R-18` (live secrets in the repository, which is the only item here that is *already* a live problem
rather than a future one).

`R-03` and `R-11` are the two with **medium residual risk after mitigation**, and both are product
judgements rather than engineering problems. They should be raised explicitly rather than absorbed.

---

## 2. Trade-off ledger

What each significant decision cost us, and what would make us revisit it.

### Postgres-as-a-queue instead of a broker

| | |
|---|---|
| **Gained** | One stateful dependency instead of two. A durable job store is required by REQ-IDEM-02 regardless, so this is genuinely free. Transactional writes across job, page, artifact and outbox rows — which is exactly what INVARIANT-1 needs. |
| **Gave up** | Broker features: priority classes, native dead-letter exchanges, fan-out to multiple consumers, mature queue-visibility tooling. |
| **Revisit when** | Sustained enqueue > ~50 jobs/s, claim-query p99 > 50 ms, or priority classes become necessary. The `JobStore` port isolates the change to one adapter. [ADR-004](19-adrs.md) |

### Modular monolith with worker processes instead of microservices

| | |
|---|---|
| **Gained** | One image, one test suite, one correlation model, shared DTOs. No network hop between stages that share a scratch directory. |
| **Gave up** | Per-stage crash isolation and per-stage scaling. A memory leak in the TTS adapter affects alignment too. |
| **Revisit when** | A stage needs a GPU; a second consumer wants one stage in isolation; or one stage's scale envelope diverges by >10×. [ADR-002](19-adrs.md) |

### Pipeline S as the V1 default

| | |
|---|---|
| **Gained** | 1–2 paid calls per page instead of 65–200. The only path that can produce the mandated timing artifact. All 56 existing tests apply to it. The free text-layer path covers 25 of 36 sample books. |
| **Gave up** | Pipeline W's blind-crop verification, per-word visual diacritics and `UnsafeReviewError` envelope. **A measurable OCR quality reduction.** |
| **Revisit when** | All four [ADR-009](19-adrs.md) criteria hold — and `Q-07` is the gate. |

### No engine-owned artifact store

| | |
|---|---|
| **Gained** | One owner for every artifact, no divergence, nothing extra to secure or pay for, workers stateless in the dimension that matters. |
| **Gave up** | **We cannot re-serve an artifact.** A Backend-side loss means re-running the job and re-paying for the AI. Artifact-level regression testing needs a download. Debugging "this audio sounds wrong" needs their copy or a local re-run. |
| **Revisit when** | Artifact-level regression testing becomes a requirement. [ADR-006](19-adrs.md) |

### WAV instead of a compressed codec

| | |
|---|---|
| **Gained** | Exact conformance to the enumerated `audio/wav` content type. No transcoding step, no extra dependency (`ffmpeg`/`pydub` are not currently needed anywhere). |
| **Gave up** | **3× the storage and bandwidth.** Measured: 1.0 MB/page median; `README.md:186-190` puts 1,000 books at 36 GB WAV vs 12 GB MP3. Audio is 86% of everything we upload. |
| **Revisit when** | `Q-09` is answered. `T-8.6`. |

### Page-level concurrency instead of strictly sequential pages

| | |
|---|---|
| **Gained** | A 30-page book finishes in minutes, not half an hour. The PDF explicitly delegates the choice to us. |
| **Gave up** | Provider friendliness. Concurrency multiplies the rate at which we approach a quota wall — and `README.md:491` shows those walls are real. Also more scratch disk in flight. |
| **Mitigated by** | A **fleet-wide** provider budget in Postgres, not a per-process limit. Scaling workers without raising the budget achieves nothing, which is the correct incentive. |

### In-memory token cache instead of a shared one

| | |
|---|---|
| **Gained** | Exact conformance to REQ-AUTH-03 ("no need to store such a token in db or other place"). No shared-cache dependency. Simple. |
| **Gave up** | N worker replicas each hold their own token, so the token endpoint sees ~N requests per 270 s instead of 1. |
| **Assessment** | Negligible — N is single digits and the endpoint is designed for this. Single-flight refresh within each process is the part that actually matters, and it is required. |

### Four mechanisms for INVARIANT-1 instead of one

| | |
|---|---|
| **Gained** | Coverage of four genuinely distinct failures: a page erroring, a worker dying after uploading, a worker dying before deciding, and a callback the Backend cannot accept. |
| **Gave up** | More machinery than a first version usually carries — a ledger, an outbox, leases and a reconciler, each with its own tests and operational surface. |
| **Assessment** | Justified only because the failure it prevents is the worst in the system: a book stalled forever, invisible from our side, discovered by the other team. Challenged directly in [C-25](24-architecture-challenge.md), and the challenge is partly conceded there. |
