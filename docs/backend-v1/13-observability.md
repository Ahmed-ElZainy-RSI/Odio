# Observability Architecture

## 1. The baseline we are starting from

Stated plainly, because it explains why several numbers in this document set are estimates:

- **Logging is `print()`.** There is no logger, no level, no correlation id anywhere.
  `walidapro/ocr_verifier/final_context_reviewer.py` alone has ~20 bare `print()` calls
  (`:731`, `:770-800`, `:896-1010`). Under any concurrency these interleave unattributably.
- **There is zero timing instrumentation in the entire repository.** No `perf_counter`, no elapsed
  logging, no report file carrying wall-clock numbers. `time` is imported only for `sleep`. This is why
  every latency figure in these documents is labelled an estimate, and why the *only* measured timings
  we have are ones taken by hand during this analysis.
- **There are no metrics, no health checks and no cost counters.** Nothing counts paid API calls, at
  either page or job level.
- **The two best quality signals already exist and are thrown away.** `bind_report`
  (`anchored`/`interpolated`/`untimed`/`monotonic`, written at `src/pipeline/run_speak_gemini.py:52-57`)
  and `FinalReviewResult.review_required` are exactly the telemetry a production system wants. They are
  written to disk per page and never aggregated.

Closing the timing gap is a **Phase 0** task (`T-0.3`), not a nice-to-have: you cannot enforce a page
deadline you cannot measure, and you cannot size a worker fleet from estimates.

## 2. Logging

### Schema

Structured JSON, one object per line, to stdout. The container runtime ships it; the engine writes no
log files.

```json
{
  "ts": "2026-09-04T10:41:02.554Z",
  "level": "INFO",
  "service": "ai-engine",
  "role": "worker",
  "version": "1.0.0",
  "event": "page.stage.completed",
  "job_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "job_type": "PAGES_IMG_AND_TEXT_EXTRACTION",
  "page_number": 7,
  "stage": "ocr",
  "duration_ms": 4213,
  "error_code": null,
  "model": "gemini-3.6-flash",
  "model_version": "gemini-3.6-flash",
  "attempt": 1
}
```

| Field | Always? | Notes |
|---|---|---|
| `ts` | ✔ | RFC 3339, UTC, milliseconds |
| `level` | ✔ | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` |
| `service`, `role`, `version` | ✔ | `role` ∈ `api` \| `worker` \| `reconciler` |
| `event` | ✔ | Stable dotted name. Never interpolate values into it — that is what fields are for. |
| `job_id` | whenever known | **The primary correlation key.** |
| `job_type` | whenever known | |
| `page_number` | page-scoped events | The secondary correlation key. |
| `stage` | stage events | `fetch` \| `render` \| `ocr` \| `diacritize` \| `tts` \| `align` \| `upload` \| `callback` |
| `duration_ms` | completion events | |
| `error_code` | ✔ (null when none) | From the taxonomy in [14-error-model.md](14-error-model.md) |
| `model`, `model_version` | inference events | So a regression after a model change is attributable |
| `request_id` | API events | From `X-Request-Id` or generated |
| `attempt` | retryable operations | |

### Correlation model

| Scope | Key | Where it comes from |
|---|---|---|
| One HTTP request to us | `request_id` | `X-Request-Id` header, else a generated ULID. Echoed in the response and in the error envelope. |
| **One job, end to end** | `job_id` | The Backend's own GUID. This is the crucial choice: **the correlation key is a value the Backend already knows**, so when they say "book X is stuck", `job_id` is the only thing anyone needs to type. |
| One page | `(job_id, page_number)` | |
| One outbound call | `job_id` + `stage` + `attempt` | |

Because `job_id` is the Backend's GUID, cross-team debugging needs no id mapping table and no shared
tracing system. That is worth more in practice than distributed tracing would be.

### Example log lines

```json
{"ts":"2026-09-04T10:15:32.881Z","level":"INFO","role":"api","event":"job.accepted",
 "job_id":"3f2504e0-...","job_type":"PAGES_IMG_AND_TEXT_EXTRACTION","duplicate":false,
 "caller_client_id":"reading-first-backend","request_id":"01J8Z3K7QF2E9V0S6M4B1TX7YD"}
```

```json
{"ts":"2026-09-04T10:15:40.112Z","level":"INFO","role":"worker","event":"job.metadata.sent",
 "job_id":"3f2504e0-...","pages_count":30,"duration_ms":181}
```

```json
{"ts":"2026-09-04T10:16:22.004Z","level":"INFO","role":"worker","event":"inference.completed",
 "job_id":"3f2504e0-...","page_number":7,"stage":"align","model":"faster-whisper-small",
 "duration_ms":5421,"anchor_ratio":0.889,"tokens":45,"anchored":40,"interpolated":5,"untimed":0}
```

```json
{"ts":"2026-09-04T10:17:03.771Z","level":"ERROR","role":"worker","event":"callback.failed",
 "job_id":"3f2504e0-...","page_number":7,"stage":"callback","error_code":"CALLBACK_FAILED",
 "attempt":4,"http_status":503,"next_attempt_at":"2026-09-04T10:18:15Z"}
```

```json
{"ts":"2026-09-04T11:02:00.010Z","level":"ERROR","role":"reconciler","event":"page.force_failed",
 "job_id":"3f2504e0-...","page_number":27,"error_code":"PROCESSING_TIMEOUT",
 "reason":"page did not complete within the job deadline","stranded_seconds":3600}
```

### Redaction

**Never logged, at any level:** the inbound bearer token, the Backend bearer token,
`AI_ENGINE_CLIENT_SECRET`, any vendor API key, `sourceDownloadUrl`, or any presigned upload URL.
Presigned URLs are bearer credentials for a specific object; logging one is equivalent to logging a
password with a long expiry.

Enforced structurally rather than by discipline: the logger runs a redaction filter over every record,
and `FailureReason` redacts at construction ([10-data-contracts.md](10-data-contracts.md) §4) so no code
path can build an un-redacted reason and ship it.

## 3. Metrics

Prometheus, exposed at `GET /metrics`. Names are fixed here; every other document in this set refers to
these exact names.

| Metric | Type | Labels | Question it answers |
|---|---|---|---|
| `ai_engine_jobs_accepted_total` | counter | `job_type`, `outcome` ∈ `new`\|`duplicate`\|`rejected` | Is the Backend's traffic arriving? How much is retry? |
| `ai_engine_job_duration_seconds` | histogram | `job_type`, `outcome` | How long does a book take end to end? |
| `ai_engine_pages_total` | counter | `job_type`, `outcome` ∈ `completed`\|`failed`\|`force_failed` | Are pages succeeding? |
| `ai_engine_page_duration_seconds` | histogram | `job_type`, `stage` | Where does a page's time actually go? **This metric is the one that turns the estimates in these docs into facts.** |
| `ai_engine_queue_depth` | gauge | `job_type` | Is work piling up? |
| `ai_engine_queue_oldest_age_seconds` | gauge | — | Is anything starving? |
| `ai_engine_inference_duration_seconds` | histogram | `provider`, `model`, `stage` | Which provider is slow? |
| `ai_engine_inference_failures_total` | counter | `provider`, `model`, `error_code` | Which provider is broken, and how? |
| `ai_engine_inference_calls_total` | counter | `provider`, `model`, `stage` | **Cost.** The only cost visibility that will exist — nothing counts paid calls today. |
| `ai_engine_external_call_duration_seconds` | histogram | `target` ∈ `backend`\|`s3`\|`keycloak`, `operation` | Is the Backend slow? |
| `ai_engine_callback_attempts_total` | counter | `endpoint`, `outcome` | Are callbacks landing? |
| `ai_engine_outbox_pending` | gauge | — | **Is the Backend being stranded right now?** |
| `ai_engine_token_refresh_total` | counter | `outcome` | Is single-flight working? A rate ≫ 1 per 270 s per replica means it is not. |
| `ai_engine_alignment_anchor_ratio` | histogram | `align_method`, `tts_model` | **Timing quality.** Corpus baseline **0.78 measured** over 95 pages. |
| `ai_engine_diacritization_review_ratio` | histogram | `tier` | Diacritization quality; the verified tier's own gate fires at ≥0.50 (`walidapro/run_full_pipeline.py:1712`). |
| `ai_engine_scratch_bytes` | gauge | — | Disk headroom. |
| `ai_engine_model_load_seconds` | histogram | `model` | Cold-start cost. Baseline: **1.54 s warm, 2.56 s with import, measured** — plus a 464 MB download if the cache volume is missing. |

## 4. SLIs and alerts

Ordered by how badly the thing hurts.

| # | Signal | Condition | Severity | Why |
|---|---|---|---|---|
| 1 | `ai_engine_pages_total{outcome="force_failed"}` | **> 0, ever** | **page** | The reconciler had to intervene, which means a worker died mid-page. In healthy operation this is exactly zero. |
| 2 | `ai_engine_outbox_pending` | > 0 for > 5 min | **page** | A book is being stranded in the Backend right now. This is the INVARIANT-1 alarm. |
| 3 | `job_pages` in `RUNNING` past `lease_until` | > 0 for > 2 min | warn | Stuck pages; precursor to #1. |
| 4 | `ai_engine_queue_oldest_age_seconds` | > 3600 | warn | Work is starving — workers down, or the provider budget is exhausted. |
| 5 | `ai_engine_callback_attempts_total{outcome="failure"}` rate | > 1% over 15 min | warn | The Backend or our token handling is unhealthy. |
| 6 | `ai_engine_inference_failures_total` rate | > 5% per provider over 15 min | warn | Provider incident; distinguish 429 (budget) from 5xx (outage) by `error_code`. |
| 7 | `ai_engine_alignment_anchor_ratio` fleet p50 | < 0.60 | warn | Timing quality regression. Baseline 0.78; per-book values legitimately range 0.51–0.94 (`README.md:473,491`), so **alert on the fleet, never on a page**. |
| 8 | `ai_engine_token_refresh_total{outcome="failure"}` | > 0 | **page** | We cannot talk to the Backend at all. |
| 9 | `ai_engine_jobs_accepted_total{outcome="rejected"}` | sudden spike | warn | Contract drift or an auth misconfiguration on either side. |
| 10 | `ai_engine_inference_calls_total` | daily total above budget | warn | Cost guard, especially if the verified tier is ever enabled — it is 65–200 calls/page against fast's 1–2. |
| 11 | `ai_engine_scratch_bytes` | > 80% of volume | warn | Orphan sweep is failing. |

## 5. Health checks

| Endpoint | Semantics | Checks |
|---|---|---|
| `GET /health` | **Liveness.** Always 200 while the process runs. | **None.** A dependency check here would restart every container during a Postgres blip — strictly worse than serving 503 from `/ready`. |
| `GET /ready` | **Readiness.** 200 or 503 with a per-dependency breakdown. | `api`: Postgres `SELECT 1` (2 s timeout), JWKS available. `worker`: Postgres, plus local model weights warmed. |

**Deliberately not in `/ready`: Gemini and LightOn reachability.** During a provider outage, accepting
jobs into a durable queue is still exactly the right behaviour — the work will run when the provider
returns. Removing the API from the load balancer would instead make the Backend's POSTs fail and its
retries pile up. Provider health belongs in alerts #6, not in readiness. Detail in
[07-api-specification.md](07-api-specification.md) §1.3.

## 6. Tracing

**PROPOSAL, optional for V1.** OpenTelemetry spans: `job` → `page` → `stage`, with the provider calls as
child spans, `job_id` and `page_number` as span attributes.

What you lose without it: the per-page waterfall. You will still know *how long* each stage took
(`ai_engine_page_duration_seconds{stage}`) and you will still be able to reconstruct a page's history
from logs filtered by `(job_id, page_number)`. What you will not get cheaply is the visual overlap
picture across concurrent pages.

Given that page stages are strictly sequential within a task, the metric-plus-log combination covers
most of the value. Recommendation: skip it in V1, revisit if page latency becomes the problem.

## 7. Day-2 operations

The five questions someone will actually ask, and the exact thing that answers each.

**1. "The Backend says book X is stuck. What is happening?"**

```text
GET /api/jobs/{jobId}          → pages.nonTerminal tells you which pages have not reported
logs: job_id={jobId}           → the full history in one filter
```
`GET /api/jobs/{jobId}` exists for precisely this ([07-api-specification.md](07-api-specification.md)
§1.5). Without it this question requires database access.

**2. "Is anything stranded right now?"**

```promql
ai_engine_outbox_pending > 0
sum(ai_engine_pages_total{outcome="force_failed"})
```
Both zero → no book is stranded. This is the single most important dashboard panel.

**3. "Why is this book slow?"**

```promql
histogram_quantile(0.9, sum by (stage,le) (rate(ai_engine_page_duration_seconds_bucket[1h])))
```
Then look at `ai_engine_inference_duration_seconds{provider,model}` for the dominant stage. Expect
`align` (CPU, RTF 0.20 measured) and `tts` (remote) to dominate the fast tier.

**4. "Are we about to hit a provider wall?"**

```promql
rate(ai_engine_inference_calls_total[1d])
sum by (provider,error_code) (rate(ai_engine_inference_failures_total{error_code="RATE_LIMITED"}[1h]))
```
Not hypothetical: `README.md:491` records a real free-tier cap of 10 requests/day per model, and Hume
quota exhausted mid-book.

**5. "Did the audio quality regress after we changed the TTS model?"**

```promql
histogram_quantile(0.5, sum by (tts_model,le) (rate(ai_engine_alignment_anchor_ratio_bucket[6h])))
```
Because the histogram is labelled by `tts_model`, the before/after comparison is one query. The reference
points are measured: 0.78 fleet baseline, 0.937 on the Hume native-timestamp book, 0.510 on the
Gemini+whisper book.
