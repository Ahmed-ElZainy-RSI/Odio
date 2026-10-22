# Open Questions

A decision register. Each entry is labelled:

- **REQUIREMENT-AMBIGUITY** — the V1 PDF is silent or unclear. Only the Backend team or product can
  answer.
- **DESIGN-CHOICE** — the PDF leaves it to us, and we simply have not decided or need input.

"Blocking" means: *blocks implementation* (we cannot write the code), *blocks integration* (we can build
but not go live), or *can default* (we proceed on a stated assumption).

## Summary

| Blocking level | IDs |
|---|---|
| **Blocks implementation** | `Q-01` |
| **Blocks integration** | `Q-04`, `Q-05` |
| Can default, but answer before go-live | `Q-02`, `Q-03`, `Q-06`, `Q-07`, `Q-08`, `Q-08b`, `Q-10`, `Q-14`, `Q-15`, `Q-17`, `Q-18` |
| Can default | `Q-09`, `Q-11`, `Q-12`, `Q-13`, `Q-16` |

---

## Authentication and transport

### `Q-01` — How does the AI Engine validate the inbound bearer token?

**REQUIREMENT-AMBIGUITY. Blocks implementation. The single largest gap in the spec.**

| | |
|---|---|
| **Question** | Which Keycloak client does the Backend present? What `aud`/`azp` should we require? Is RS256 correct? Where is the JWKS endpoint? Is any scope or realm role expected? |
| **Why it matters** | The PDF gives us `client_id`/`client_secret` **for calling the Backend** and states that inbound calls carry `Authorization: Bearer {ai-engine-bearer-token}` — but never says how we verify it. We cannot write `T-2.2` without this. |
| **Who answers** | Backend team (Rashad) |
| **Proposed default** | Keycloak JWKS validation: RS256 only, JWKS from `{realm}/protocol/openid-connect/certs` with a TTL cache, `iss`/`exp`/`nbf` with 30 s leeway, `azp`/`aud` against `BACKEND_ALLOWED_CLIENT_IDS`. 401 for identity failures, 403 for a valid token from an unlisted client. See [12-security.md](12-security.md) §2. |
| **Impact if wrong** | Either every request is rejected (a hard failure at the first integration test) or, far worse, we accept tokens we should not — an authentication bypass. |
| **Note** | The engine's own `client_secret` is an **outbound** credential. It must never be used as an inbound shared secret; that would give either service the ability to impersonate the other and require synchronized rotation. Explicitly rejected in [ADR-007](19-adrs.md). |

### `Q-15` — TLS posture, and is the token endpoint really plain HTTP?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Question** | The PDF's examples are `https://localhost:9099` and `https://localhost:6060`, but `token_URL` is `http://localhost:9090/...`. Is the token endpoint plain HTTP in production? Where is TLS terminated? Do you want mTLS between the two services? |
| **Why it matters** | `client_credentials` sends `client_secret` in the request body. Over plain HTTP, anything on the path can read it. |
| **Who answers** | Backend team / infrastructure |
| **Proposed default** | TLS end to end in production; the `http://` in the spec is a local-development artifact. mTLS deferred to future hardening. |
| **Impact if wrong** | The highest-value secret in the system is exposed in transit. |

---

## Artifact semantics

### `Q-04` — Is the `Text` artifact raw OCR text, or final corrected + diacritized reading text?

**REQUIREMENT-AMBIGUITY. Blocks integration.**

| | |
|---|---|
| **Question** | The PDF says only *"this file will be only the free text no highlighting no timing needed at this step"*. Does "free text" mean the raw OCR output, or the final text a reader would display — corrected and with Arabic diacritics? |
| **Why it matters** | It defines the scope of both job types. This file is also **Job B's input**, and undiacritized Arabic is mispronounced by TTS. If it is raw text, either Job A must deliver diacritized text through some other channel, or Job B must diacritize — which changes what Job B costs and what it can guarantee. |
| **Who answers** | Backend team + product |
| **Proposed default** | **ASSUMPTION A3**: the final reading text, i.e. `" ".join(t["txt"] for t in tokens)` where `txt` is corrected and diacritized. |
| **Impact if wrong** | Job A's artifact is wrong, Job B's TTS quality degrades badly, and the reader displays the wrong text. Rework in both jobs. |

### `Q-10` — What does the reader app need from the timing JSON? Does it need bounding boxes?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Question** | The PDF says "timing json" and nothing else — no schema, no fields. Separately: does the reader do **image-view** highlighting, where a word is highlighted on the page image as it is spoken? |
| **Why it matters** | We propose a self-contained schema ([10-data-contracts.md](10-data-contracts.md) §6.2) and will build to it. But bounding boxes are the real issue: they exist in Job A's data (`tokens[].box`, present on 4,749 of 7,295 real tokens) and **the V1 contract has no `artifactType` slot for them at all.** If the reader needs image-view highlighting, that is a **contract gap**, not an implementation gap. |
| **Who answers** | Backend team + the reader app's owner |
| **Proposed default** | Our proposed schema, `schema_version: "1.0"`, no boxes. |
| **Impact if wrong** | A schema mismatch is a small rework. A missing boxes artifact is a **new contract**, and note that the LightOn OCR path produces no geometry at all (`box_level: "none"`), so not every book could supply them even if the contract existed. |

### `Q-08b` — One audio file per page, or could you accept two?

**DESIGN-CHOICE. Answer before go-live.**

| | |
|---|---|
| **Question** | The `completed` payload carries a single `Audio` artifact. The existing pipeline synthesizes **two** narrators per page — female then male (`walidapro/run_full_pipeline.py:1057-1130`). Which do you want, and should the choice be per book? |
| **Why it matters** | Dual narration doubles TTS cost and bytes, and currently has nowhere to go in the contract. |
| **Who answers** | Product |
| **Proposed default** | One artifact, female narrator, selected by `TTS_NARRATOR`. |
| **Impact if wrong** | Half the generated audio is discarded, or a second artifact type is needed. |

### `Q-09` — Is `audio/wav` required, or merely permitted?

**REQUIREMENT-AMBIGUITY. Can default.**

| | |
|---|---|
| **Question** | `audio/wav` is in your content-type list. Is it a requirement, or would `audio/mpeg` be acceptable? |
| **Why it matters** | WAV is **~1.0 MB per page median, 1.8 MB at p90, 8.9 MB worst observed** (measured over 95 real files). `README.md:186-190`'s own scale table puts 1,000 books at **36 GB WAV versus 12 GB MP3**. Audio is 86% of everything we upload. |
| **Who answers** | Backend team |
| **Proposed default** | WAV, since it is the enumerated type. |
| **Impact if wrong** | 3× more storage and bandwidth than necessary, indefinitely. |

### `Q-11` — Are HTML reader artifacts deliverables?

**DESIGN-CHOICE. Can default.**

| | |
|---|---|
| **Question** | The pipeline's most visible product today is `reader.html` — a self-contained karaoke reader (`src/pipeline/build_reader.py:58`). Your `artifactType` enum has no slot for HTML. Should it? |
| **Proposed default** | No. HTML readers remain a local development and QA artifact. |
| **Impact if wrong** | A new artifact type and a fifth upload per page. |

---

## Protocol semantics

### `Q-05` — Is `pageNumber` 1-based? Is it the physical PDF page index?

**REQUIREMENT-AMBIGUITY. Blocks integration.**

| | |
|---|---|
| **Why it matters** | A one-off error mislabels every artifact in every book, and INVARIANT-1's page set would be wrong by one. |
| **Proposed default** | **ASSUMPTION A1**: 1-based, physical PDF page index. Your own example (`pagesCount: 30`, *"the backend will wait for number 30 page"*) implies `1..30`, and the pipeline is 1-based throughout — `src/pipeline/pages.py:4-5` documents "1-indexed, inclusive, matching the `p001.json` page numbering used throughout `work/<book>/`". |
| **Impact if wrong** | Every artifact is attached to the wrong page. |

### `Q-06` — Is `pagesCount` always the full PDF page count? Could a Job A cover a subrange?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Why it matters** | It defines what INVARIANT-1 is quantified over. Also: Job A is one job covering N pages while Job B is one job covering **one** page — is that asymmetry intentional? Does `metadata` apply to Job B at all? |
| **Proposed default** | **ASSUMPTION A2**: `pagesCount` is the full page count of the delivered PDF; Job A has no subrange concept; Job B sends no `metadata` and its single page callback is also its completion signal. |
| **Impact if wrong** | We would send `metadata` for the wrong count and the Backend would wait for pages we never intend to produce. |

### `Q-02` — Is `POST /api/files/{fileId}/confirm-upload` idempotent? Does a retried `initialize-upload` orphan the first `fileId`?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Why it matters** | We must retry on 5xx and timeouts, so a double confirm **will** happen. And a page retried after a mid-cycle crash mints a second `fileId` for the same logical file — traced in [09-async-jobs.md](09-async-jobs.md) §7.5. |
| **Proposed default** | Assume both are safe; record every minted `fileId` so orphans are at least traceable. |
| **Impact if wrong** | Either a double confirm is an error we must avoid (requiring a different upload strategy), or orphaned files accumulate in your storage. |

### `Q-03` — Is `PATCH /api/jobs/{jobId}/{pageNumber}/completed` idempotent? And `/failed`?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Why it matters** | Callback delivery is at-least-once, so you may occasionally see a duplicate — specifically when you processed our PATCH but we never saw the 200. **If a duplicate `completed` double-counts pages on your side, INVARIANT-1 breaks from your end**, which is the exact failure the whole outbox design exists to prevent. |
| **Proposed default** | Assume idempotent. Our drainer never re-sends a row it has seen acked, so duplicates are rare rather than routine. |
| **Impact if wrong** | Books could be marked complete early, or stall. |

### `Q-16` — What happens if we PATCH a page number greater than `pagesCount`, or one already reported?

**DESIGN-CHOICE. Can default.**

| | |
|---|---|
| **Why it matters** | Purely defensive — it tells us how much our own guardrails matter and whether a bug on our side would be visible or silent. |
| **Proposed default** | We never do it. Our ledger has a `(job_id, page_number)` primary key and the `one_callback_per_target` unique constraint, so it is structurally prevented. |

### `Q-14` — Is `reason` free text for humans, or do you parse it?

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Why it matters** | Determines whether we can reword messages later without breaking you. |
| **Proposed default** | `<ERROR_CODE>: <human explanation>`, ≤512 chars, never containing a URL, token or stack trace. Examples in [14-error-model.md](14-error-model.md) §3. |

### `Q-17` — Same `jobId`, different body: `202` (the spec's wording) or `409` (what we return)?

**REQUIREMENT-AMBIGUITY. Answer before go-live.** Reopened 2026-10-22 by spec 030: it was listed below as answered, but the V1 PDF says the opposite.

| | |
|---|---|
| **What the spec says** | *"if it has [this GUID] then it should return success"*, and *"any fail means exponential backoff retrying from backend and after a while stalled/dead letter."* |
| **What we do** | `409 CONFLICT` when the body differs (`sourceDownloadUrl` excluded, since it legitimately changes between retries). walidapro returns `202` regardless, which follows the spec's letter. |
| **Why it matters** | `202` silently drops a second, different job reusing a GUID. `409` turns that Backend bug into a retry storm and then a dead letter. Both are defensible; only the Backend can say which failure they'd rather see. |
| **Proposed default** | Keep `409`: a lost job is invisible, a dead letter is visible. Pinned by `service/tests/behaviour/test_walidapro_scenarios.py`. |

### `Q-18` — `400` or `422` for a malformed job request?

**DESIGN-CHOICE. Can default, answer before go-live.**

| | |
|---|---|
| **Why it matters** | The spec defines no error codes for `POST /api/jobs`. We answer every body-validation failure (unknown `jobType`, missing `pageNumber`, non-GUID `jobId`, missing `folderId`/`pathPrefix`) with `422`; walidapro used `400`. If the Backend's retry logic treats one as retryable and not the other, the choice matters. |
| **Proposed default** | `422`, never retried by a sane client. Ask the Backend to treat any `4xx` except `401`/`429` as permanent. |

---

## Scope, quality and operations

### `Q-07` — Is V1 accepted on protocol conformance, or on AI output quality?

**DESIGN-CHOICE. Answer before go-live. This one changes which engine ships.**

| | |
|---|---|
| **Why it matters** | The most consequential question in this register after `Q-01`. Two pipelines exist. Pipeline S is 1–2 paid calls per page, has all the tests, and is **the only one that can produce the mandated timing artifact** (zero timing hits across all `walidapro/**/*.py`). Pipeline W has materially better OCR correction but costs **65–200 paid calls per page** — dominated by one vision call *per Arabic word* (measured: median 35, p90 69, max 137 words/page) — and has **zero tests across 16,155 LOC**. |
| **Who answers** | Product + both team leads |
| **Proposed default** | Protocol conformance. Ship Pipeline S; keep Pipeline W behind an opt-in flag. [ADR-009](19-adrs.md) |
| **Impact if wrong** | If acceptance is on accuracy, V1 as designed will be judged inadequate, and enabling the verified tier requires two prerequisite projects first: parallelizing the per-word loop (`T-8.1`) and giving `walidapro/` a test suite (`T-8.2`). |

### `Q-08` — Presigned URL TTL, maximum `sizeInBytes`, and behaviour on a 0-page/encrypted/corrupt PDF

**REQUIREMENT-AMBIGUITY. Answer before go-live.**

| | |
|---|---|
| **Why it matters** | The TTL sizes our retry windows and tells us how eagerly to refresh. The size cap tells us whether an 8.9 MB WAV (observed) is fine or must be rejected before upload. The corrupt-PDF behaviour is presumably whole-job `/failed`, but it is unstated. |
| **Proposed default** | Retry within 300 s then refresh; `MAX_SOURCE_BYTES` 200 MB; `MAX_SOURCE_PAGES` 500; any unparseable, encrypted or 0-page source → whole-job `/failed` with `SOURCE_INVALID`. |

### `Q-12` — Retention, deletion, and re-run semantics

**DESIGN-CHOICE. Can default.**

| | |
|---|---|
| **Question** | How long should we keep job records? What should happen when the same book is submitted again under a new `jobId` — do you want us to detect it, or produce a second independent set of files? Is there any deletion request we must honour? |
| **Proposed default** | Terminal job rows kept 90 days; delivered outbox rows 7 days; logs 30 days. **No content-based deduplication** — a new `jobId` is a new job and produces new files. We retain no artifacts at all ([ADR-006](19-adrs.md)), so a deletion request is entirely yours to service. |

### `Q-13` — Any latency, throughput, availability or concurrency target?

**REQUIREMENT-AMBIGUITY. Can default.**

| | |
|---|---|
| **Question** | The PDF specifies none. Expected book volume per day? An acceptable end-to-end latency for a book? Any availability expectation? Should we rate-limit you? |
| **Why it matters** | It sizes the worker fleet and sets the provider budget. Note the PDF explicitly delegates parallelism to us ("in parallel or sequential … It is a team cause depending on their resources"), so we will choose — we would just rather choose against a real number. |
| **Proposed default** | Best-effort, no promised SLA. Design target 4 concurrent jobs and 8 concurrent page tasks per host. No inbound rate limiting (one trusted caller, and a limiter would most likely fire during your retry storm — exactly when we least want to reject work). |
| **Impact if wrong** | Under-provisioning shows up as a growing `ai_engine_queue_oldest_age_seconds`, which is alerted on, so this fails visibly rather than silently. |

---

## Questions we answered ourselves

Recorded so nobody re-opens them, and so the reasoning is auditable.

| Question | Answer | Where |
|---|---|---|
| Modular monolith, workers, or microservices? | Modular monolith + worker processes | [ADR-002](19-adrs.md) |
| Broker or Postgres queue? | Postgres `SKIP LOCKED`; a durable store is required by REQ-IDEM-02 anyway, so a broker would be a second stateful dependency | [ADR-004](19-adrs.md) |
| Which pipeline for V1? | Pipeline S default, Pipeline W opt-in | [ADR-009](19-adrs.md) |
| Do we need a GPU? | **No.** Every local model is CPU-pinned by hardcoded choice | [18-deployment.md](18-deployment.md) §4 |
| Do we own an artifact store? | No; the Backend owns storage | [ADR-006](19-adrs.md) |
| Do any AI stages become endpoints? | **No.** V1 has exactly one API surface | [08-ai-integration.md](08-ai-integration.md) §3 |
| ~~Same `jobId`, different body?~~ | **Reopened as `Q-17`** (2026-10-22): the V1 PDF says to return success | [Q-17](#q-17--same-jobid-different-body-202-the-specs-wording-or-409-what-we-return) |
| Do pages run concurrently within a job? | Yes, bounded by page concurrency **and** a fleet-wide provider budget | [09-async-jobs.md](09-async-jobs.md) §6 |
| Is the human-in-the-loop correction path in V1? | No — it is human-gated by design (`tools/context_detector.py:148-160` defaults every decision to `"reject"`) | [04-gap-analysis.md](04-gap-analysis.md) §2 |
