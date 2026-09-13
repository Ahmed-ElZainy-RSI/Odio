# Operations

## Logs

Structured JSON, one object per line, to stdout. The container runtime ships it; the service
writes no log files.

```json
{"ts":"2026-09-07T10:41:02.554Z","level":"info","service":"ai-engine","role":"worker",
 "version":"0.1.0","event":"page.completed","job_id":"3f2504e0-4f89-11d3-9a0c-0305e82c3301",
 "job_type":"PAGES_IMG_AND_TEXT_EXTRACTION","page_number":7}
```

### Correlation

**`job_id` is the primary key across the entire lifecycle, and it is deliberately the
Backend's own GUID.** When they say "book X is stuck", that value is the only thing anyone
has to type — no id mapping table, no shared tracing system. In practice that is worth more
than distributed tracing would be.

| Scope | Field |
|---|---|
| One HTTP request to us | `request_id` — from `X-Request-Id`, else generated; echoed in the response header and the error envelope |
| One job, end to end | `job_id` |
| One page | `job_id` + `page_number` |
| One stage | `+ stage` — `fetch`, `render`, `ocr`, `upload`, `callback` |

### Events worth knowing

| Event | Level | Means |
|---|---|---|
| `job.accepted` | info | A job was accepted. `duplicate` says whether it was new. |
| `job.rejected.conflict` | warn | Same `jobId`, different payload — a caller bug |
| `job.quality_tier.downgraded` | warn | A `verified` request was downgraded by the kill-switch |
| `job.metadata_delivered` | info | The page-count gate opened; page work may start |
| `page.completed` / `page.failed` | info / warn | A page reached a terminal report |
| `page.force_failed` | **error** | **The reconciler reported a page a worker abandoned.** A worker died mid-page. |
| `outbox.parked` | **error** | **A callback could not be delivered. A book is stranded and a human must intervene.** |
| `source.expired` | info | A presigned URL 403'd; refreshing |
| `http.unauthorized.refreshing` | info | A 401; forcing a token refresh and retrying once |
| `auth.not_enforced` | warn | Emitted **every boot** while `Q-01` is unresolved |
| `job.invariant_violation` | **error** | Pages did not all report. Should be impossible; investigate. |
| `job.resumed` | info | A claimed job already had reported pages; they are being skipped |
| `ocr.engine_selected` | info | Which OCR engine this document resolved to. `auto` decides per document. |
| `provider_budget.exhausted` | warn | A paid call was refused: the window's allowance is gone |
| `page.parked` | warn | A page was **not** attempted and **not** reported. Waiting for an allowance. |
| `job.deferred` | warn | The job's lease is held until a provider window resets |
| `provider_budget.not_fleet_wide` | warn | Budgets are configured but `JOB_STORE=memory`, so the counter is per-process |
| `store.not_durable` | warn | `JOB_STORE=memory`. A restart loses every in-flight job. |

### Redaction

Never logged, at any level: the inbound or Backend bearer token, `client_secret`, any vendor
API key, `sourceDownloadUrl`, or any presigned upload URL. A presigned URL is a bearer
credential — logging one is equivalent to logging a password with a long expiry.

Route-ish fields (`path`, `event`, `stage`) are exempt from pattern scrubbing, because a log
line that cannot tell you which endpoint failed is close to worthless.

## Metrics

`GET /metrics`, Prometheus exposition. Falls back to an in-process registry when
`prometheus-client` is absent, so the names stay authoritative.

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `ai_engine_jobs_accepted_total` | counter | `job_type`, `outcome` ∈ `new`\|`duplicate`\|`rejected` | Is traffic arriving? How much is retry? |
| `ai_engine_pages_total` | counter | `job_type`, `outcome` ∈ `completed`\|`failed`\|`force_failed` | Are pages finishing? |
| `ai_engine_page_duration_seconds` | histogram | `job_type`, `stage` | Where does a page's time go? |
| `ai_engine_inference_calls_total` | counter | `provider`, `model`, `stage` | **Cost.** The only cost visibility that exists. |
| `ai_engine_inference_failures_total` | counter | `provider`, `model`, `error_code` | Which provider is broken, and how? |
| `ai_engine_callback_attempts_total` | counter | `endpoint`, `outcome` | Are callbacks landing? |
| `ai_engine_outbox_pending` | gauge | — | **Is a book being stranded right now?** |
| `ai_engine_token_refresh_total` | counter | `outcome` | Is single-flight working? |
| `ai_engine_alignment_anchor_ratio` | histogram | `align_method`, `tts_model` | Timing quality. Corpus baseline 0.78. |
| `ai_engine_queue_depth` | gauge | `job_type` | Is work piling up? |
| `ai_engine_provider_budget_exhausted_total` | counter | `provider` | **Is a book parked behind a quota wall?** |
| `ai_engine_provider_budget_remaining` | gauge | `provider` | How much allowance is left in this window |

## Alerts

Ordered by how badly the thing hurts.

| # | Condition | Severity | Why |
|---|---|---|---|
| 1 | `ai_engine_pages_total{outcome="force_failed"} > 0` | **page** | The reconciler had to intervene: a worker died mid-page. **In healthy operation this is exactly zero.** |
| 2 | `ai_engine_outbox_pending > 0` for > 5 min | **page** | A book is being stranded in the Backend right now. This is *the* INVARIANT-1 alarm. |
| 3 | `ai_engine_token_refresh_total{outcome="failure"} > 0` | **page** | We cannot talk to the Backend at all — no uploads, no callbacks |
| 4 | `ai_engine_callback_attempts_total{outcome="failure"}` rate > 1% / 15 min | warn | The Backend or our token handling is unhealthy |
| 5 | `ai_engine_inference_failures_total` rate > 5% per provider | warn | Provider incident. Distinguish `RATE_LIMITED` (budget) from 5xx (outage) by `error_code`. |
| 6 | `ai_engine_alignment_anchor_ratio` fleet p50 < 0.60 | warn | Timing quality regression. Baseline 0.78; per-book values legitimately range 0.51–0.94, so **alert on the fleet, never on a page**. |
| 7 | `ai_engine_jobs_accepted_total{outcome="rejected"}` spike | warn | Contract drift or an auth misconfiguration on either side |
| 8 | `ai_engine_inference_calls_total` daily total above budget | warn | Cost guard |
| 9 | `increase(ai_engine_provider_budget_exhausted_total[1h]) > 0` | warn | Books are parked behind a provider quota. Not data loss — but nothing progresses until the window resets or the plan is raised, and `JOB_DEADLINE_SECONDS` is the only backstop. |

**If alerts 1 and 2 are both zero, no book is stranded.** That is the single most useful
sentence on the dashboard.

## Health probes

| Endpoint | Semantics |
|---|---|
| `GET /health` | Liveness. Always `200` while the process is alive. **No dependency checks** — one here would restart every container during a brief database blip. |
| `GET /ready` | Readiness. `200`/`503` with a per-dependency breakdown. |

Provider reachability is deliberately **not** a readiness signal: during a provider outage,
accepting jobs into a durable queue is still correct, so removing the API from the load
balancer would only make the Backend's retries pile up.

## Running it

One image, three roles. The role is the command.

```bash
# Context is the REPOSITORY ROOT: the service wraps the legacy pipeline in src/.
docker build -f service/Dockerfile -t ai-engine:dev .

docker compose -f service/docker-compose.yml up          # api + worker + reconciler
docker compose -f service/docker-compose.yml up --scale worker=3
```

| Role | Command | Replicas |
|---|---|---|
| `api` | `ai-engine-api` | As many as you like. It does one indexed insert and returns. |
| `worker` | `ai-engine-worker` | **Scale this.** Page work is the slow part, and leases make it safe. |
| `reconciler` | `ai-engine-reconciler` | **Exactly one.** Two racing deadline enforcers would double-report an abandoned job's pages. |

Two volumes matter:

* `/var/lib/ai-engine` — the job ledger, the callback outbox and the provider budget. Lose
  this and you lose in-flight jobs. Every role must mount the same one.
* `/models` — the whisper weights, 464 MB, downloaded once. Or bake them in with
  `--build-arg PREFETCH_WHISPER=1` for a container that starts with no cold-start download.

The image runs as a non-root user (`engine`, uid 10001) and writes only to those two paths.

The image declares **no** `HEALTHCHECK`: one image runs three roles and only `api` serves
HTTP, so an image-level probe against `:9099` would mark every worker and reconciler
unhealthy. The API's probe is on the `api` service in `docker-compose.yml` (a
`readinessProbe` on `/ready` in Kubernetes); for the worker and reconciler, liveness is the
process — they exit on an unrecoverable error and the restart policy handles it.

Worker identity is `hostname-pid`, which is the container or pod name in both Docker and
Kubernetes, so the store's `claimed_by` names the exact process holding a lease. Verified
with two worker containers on one volume: they claim different jobs, and `claimed_by`
distinguishes them.

## Day-2 questions

**"The Backend says book X is stuck. What is happening?"**

```bash
curl -s localhost:9099/api/jobs/$JOB_ID    # pages.nonTerminal names the pages
# then filter logs by job_id=$JOB_ID for the full history
```

**"Is anything stranded right now?"**

```promql
ai_engine_outbox_pending > 0
sum(ai_engine_pages_total{outcome="force_failed"})
```

Both zero → nothing is stranded.

**"Why is this book slow?"**

```promql
histogram_quantile(0.9, sum by (stage,le) (rate(ai_engine_page_duration_seconds_bucket[1h])))
```

Expect `render` and `ocr` to dominate on the free text-layer path (it is CPU, not network),
and remote calls to dominate once the paid adapters land.

**"Are we about to hit a provider wall?"**

```bash
curl -s localhost:9099/ops/budget    # used / remaining / windowReset, per provider
```

```promql
ai_engine_provider_budget_remaining
increase(ai_engine_provider_budget_exhausted_total[1h])
```

**"A book stopped halfway and nothing is failing. Why?"**

Almost certainly a parked provider budget. Check `/ops/budget` for
`"remaining": 0`, then `GET /api/jobs/{jobId}` — the job is `RUNNING` with non-terminal
pages, and no callback has been sent for them (deliberately: nothing was attempted). It
resumes on its own when `windowReset` passes. To resume sooner, raise
`PROVIDER_CALL_BUDGETS` and restart the workers.

**"Did quality regress after a model change?"**

```promql
histogram_quantile(0.5, sum by (tts_model,le) (rate(ai_engine_alignment_anchor_ratio_bucket[6h])))
```

The histogram is labelled by model, so the before/after comparison is one query.

## Failure playbook

| Symptom | Likely cause | Action |
|---|---|---|
| `outbox.parked` in the logs | The Backend has been rejecting or dropping a callback past the attempt cap | Fix the Backend-side issue, then re-drain. The row is durable, so nothing is lost. |
| `page.force_failed` > 0 | A worker died mid-page | Check for OOM kills and worker restarts. The page *was* reported, so the book is not stranded — but find out why the worker died. |
| Every job fails with `SOURCE_INVALID` | The URL is serving something that is not a PDF | Check what the presigned URL actually returns; the reason string names the magic bytes seen. |
| Every job fails with `422 host is not on the allowlist` | `SOURCE_URL_ALLOWED_HOSTS` is wrong or unset | Set it to the Backend's real storage hostnames. |
| `AUTHENTICATION_ERROR` on every Backend call | Wrong `AI_ENGINE_CLIENT_SECRET`, or the token endpoint is unreachable | A 401 from Keycloak is **never retried** by design — it means the credentials are wrong, not stale. |
| `refusing to start: .../.env exists` | A `.env` on the container filesystem | Remove it. It would silently override injected secrets. |
| Jobs accepted but nothing happens | **The API and the worker are pointed at different stores.** | They must share `JOB_STORE_PATH` — the API writes the job and the worker claims it. `JOB_STORE=memory` cannot be shared between processes at all. |
| A book stops halfway, no failures, no callbacks | A provider allowance is exhausted and the remaining pages are parked | `curl /ops/budget`. It resumes at `windowReset`; raise `PROVIDER_CALL_BUDGETS` and restart the workers to resume sooner. Nothing is lost. |
| Every page fails with `MODEL_UNAVAILABLE` naming an API key | A paid engine is selected without its credential | Set `GEMINI_API_KEY`/`LIGHTON_API_KEY`, or use `OCR_ENGINE=auto`, which needs no key for a book with a text layer. The job fails once before rendering, not once per page. |
| `schema version mismatch: database has N` | The image is older or newer than the database file | Both are stamped. Deploy the matching build, or start a fresh database — there is no migration path yet. |
| `503` from `POST /api/jobs` | The store is unreachable | Correct behaviour — the Backend should retry. Returning `202` would strand the book silently. |
