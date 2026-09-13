# Architecture Challenge ("Grill Me")

An adversarial review of everything in this document set, written from the position of a skeptical
Principal Engineer who has read the code and the PDF and is not inclined to be generous.

Each challenge is: **Concern** → **Verdict** (`REAL PROBLEM` / `PARTLY REAL` / `NOT A PROBLEM`) →
**Resolution** → **Disposition** (`REQUIRED FOR V1` / `FUTURE WORK` / `NO ACTION`).

Thirty-five challenges. The architecture does not win all of them.

---

### C-01. Why is this a service at all? Why not a library the Backend imports?

**Concern.** You are building an HTTP server, a queue, a database, an auth client and a callback outbox
to run some Python that already exists. If the Backend just `pip install`ed the pipeline, all of that
disappears.

**Verdict. NOT A PROBLEM.** Three independent blockers. (1) The Backend is not Python — nothing in the
PDF says what it is, but it exposes `PATCH`-heavy REST with Keycloak and presigned S3, and the PDF
explicitly names two teams with two owners. (2) The runtime needs 464 MB of model weights, PaddleOCR, and
CPU-saturating work for minutes per page; embedding that in the Backend's process would make their
deploys hostage to our model loads. (3) The PDF **specifies** the protocol — `POST /api/jobs` → 202,
plus five callbacks. A library does not satisfy a wire contract.

**Resolution.** None needed. But it is worth noting the honest version of this challenge: *the AI logic
is a library, and we are building a thin service around it.* That is exactly the design — the ports and
adapters exist so the library stays a library.

**Disposition. NO ACTION.**

---

### C-02. Why isn't the whole thing one internal module inside a single process?

**Concern.** Option A in [05-target-architecture.md](05-target-architecture.md) §2. One process, one
deploy, no queue semantics. You dismissed it too fast.

**Verdict. PARTLY REAL.** Option A is genuinely a serious contender at *books per day* volume, and the
document says so. The dismissal rests on one measured fact: every provider call in the existing code is a
blocking `requests.post` with `time.sleep` backoff (`walidapro/ocr_verifier/lighton_ocr.py:197`, worst
case 546 s of blocked thread), and forced alignment is 5.42 s of CPU per 27.5 s of audio. With a bounded
thread pool, a handful of concurrent books starves the HTTP handler and `POST /api/jobs` stops answering
promptly — REQ-JOB-01 satisfied in code, violated in practice.

**Resolution.** Keep the split. But note the mitigation cost is small: the API and worker share one
image and one codebase, so this is *two entrypoints*, not two services. If it turns out the API role is
pure overhead, collapsing it later is a deployment change, not a rewrite.

**Disposition. NO ACTION**, with the collapse path documented.

---

### C-03. Why REST? The Backend presumably already runs a broker. Why not consume from it?

**Concern.** A broker gives durable delivery, back-pressure, and removes the entire "can I honestly
return 202?" problem — the message stays in the broker until we ack it.

**Verdict. REAL PROBLEM — with the right answer anyway.** This is the strongest architectural criticism
in the review. Broker-based ingestion would be a *better* design: no 202-lie problem (see
[C-10](#c-10-where-is-state-and-what-if-postgres-is-unavailable-at-accept-time)), natural
back-pressure, and delivery guarantees we would not have to build.

**Resolution.** It is not available. The PDF specifies HTTP verbs, JSON bodies and status codes, and no
shared broker exists between the two teams. Introducing one is a cross-team infrastructure project that
would delay V1 by more than the design improvement is worth. Recorded honestly as a rejected
alternative in [ADR-001](19-adrs.md) rather than pretended away.

**Disposition. FUTURE WORK** — raise it for V2 if the Backend team already operates a broker.

---

### C-04. Why sync-accept? Why not have the Backend poll a status endpoint instead of you calling back?

**Concern.** Callbacks are the hard part: you need an outbox, retries, a reconciler, and you depend on
their endpoints being idempotent. Polling would push all of that onto them, where it is cheap.

**Verdict. NOT A PROBLEM, but for a non-technical reason.** Polling would genuinely be simpler for us —
one `GET /api/jobs/{jobId}` and no delivery guarantees at all. But the PDF specifies the callback
direction across five endpoints and the Backend has already built them. Unilaterally inverting the
integration direction is not a design choice available to us.

**Resolution.** Implement as specified. Note that `GET /api/jobs/{jobId}`
([07-api-specification.md](07-api-specification.md) §1.5) exists anyway, so a future poll-based fallback
is a small addition.

**Disposition. NO ACTION.**

---

### C-05. What happens when a model takes 60 seconds? What about 30 minutes?

**Concern.** You have no idea how long these calls take, because there is zero timing instrumentation in
the entire repository.

**Verdict. PARTLY REAL — and the concern about measurement is entirely correct.**

- **60 s:** fine. The page task owns its slot; nothing is blocked but that slot.
- **30 min:** cut off at `PAGE_TASK_TIMEOUT_SECONDS` (900 s) and reported as `PROCESSING_TIMEOUT`.
- **The real problem:** today there is **no page deadline anywhere**, and
  `walidapro/.../final_context_reviewer.py:825` passes no timeout at all, so the OpenAI SDK's 600 s read
  default applies — one hung call pins a worker for ten minutes. Combined with app-3× × SDK-2× retries,
  a single page's worst case is ~30 minutes and 9 billed completions.

**Resolution.** `T-0.3` adds the timing instrumentation (that is why Phase 0 exists at all), `T-1.4`
and `T-5.2` add explicit timeouts everywhere, and the page deadline is enforced by the Page Task
Executor. **Until `T-0.3` lands, every latency number in these documents is an estimate and is labelled
as one.**

**Disposition. REQUIRED FOR V1.**

---

### C-06. What happens when the model crashes mid-page?

**Concern.** Half-uploaded artifacts, a page in `RUNNING` forever, a stalled book.

**Verdict. NOT A PROBLEM.** Fully traced in [09-async-jobs.md](09-async-jobs.md) §7.5: the lease expires,
the page returns to `PENDING`, and it re-runs. The `artifacts` row is written **only after**
`confirm-upload` succeeds, so a partial upload leaves no false record. The one real cost is an orphaned
Backend `fileId` — a storage cost, not a correctness problem (`R-15`, `Q-02`).

**Resolution.** None. But note the contrast: under the current code the same crash loses the **whole
book**, because `src/pipeline/run_ocr_gemini.py:59-62` `break`s out of the page loop.

**Disposition. NO ACTION.**

---

### C-07. What happens if two requests process the same file?

**Concern.** Two jobs over the same PDF, concurrently.

**Verdict. NOT A PROBLEM by design — and this is one place the new architecture is strictly better.**
Different `jobId`s are different jobs with different scratch directories (`scratch/{job_id}`), producing
two independent sets of Backend files. No shared state, no interference.

Under the **current** code this is a data-corruption bug: identity is `work/{pdf_path.stem}`
(`src/pipeline/main.py:62-63`) with **no locking anywhere** (zero matches for
`os.replace`/`rename`/`fsync`/`flock` across `src/pipeline` and `tools`), and two runs would interleave
writes into one directory. `tools/context_detector.py`'s own comment at `:17-20` records nearly losing
paid work to exactly this class of carelessness.

**Resolution.** Whether the Backend *wants* duplicate work detected is `Q-12`. We do not deduplicate by
content.

**Disposition. NO ACTION.**

---

### C-08. What if the same `jobId` arrives twice *concurrently*? Be precise about the race.

**Concern.** "Check then insert" is a classic race. Two requests both find no row, both insert, both
schedule work, and the book is processed twice.

**Verdict. NOT A PROBLEM — because it is deliberately not a check-then-insert.**

The accept is **one statement**:

```sql
INSERT INTO jobs (job_id, ...) VALUES (...) ON CONFLICT (job_id) DO NOTHING RETURNING job_id;
```

`job_id` is the PRIMARY KEY, so the uniqueness is enforced by the index, not by application logic. Under
concurrency, exactly one transaction inserts; the other's `ON CONFLICT DO NOTHING` returns zero rows.
`RETURNING` tells us which we were, so we can set `duplicate: true`. **Both return 202** (REQ-IDEM-01).

The subtle part worth stating: `ON CONFLICT DO NOTHING` does **not** block on the concurrent inserter's
lock the way `DO UPDATE` would — it takes the no-op path. So there is no lock-wait, no deadlock risk, and
no serialization failure to retry.

**Resolution.** Tested by `test_concurrent_identical_posts_create_one_job` with 20 concurrent POSTs.

**Disposition. REQUIRED FOR V1** (the test, not a design change).

---

### C-09. What happens under concurrent load?

**Concern.** You have never run two of anything at once. The existing code is single-threaded end to end.

**Verdict. PARTLY REAL.** The concern is well-founded about the *existing* code, and the documents
enumerate exactly why: module-global model singletons (`src/pipeline/align_whisper.py:16-23`,
`tashkel.py:25-33`) that double-build under concurrent first touch; a frozen import-time config singleton
(`walidapro/ocr_verifier/config.py:11-35`); a lock-free whole-file cache rewrite with a network call in
the middle (`word_diacritics.py:1143`→`:1405`); and a shared output directory keyed on a filename.

**Resolution.** Each is a named task: `T-3.5` (singletons), `T-1.2` (config), `T-3.6` (cache), `T-4.6`
(per-job scratch). Concurrency is bounded three ways — per worker, per job, and by a fleet-wide provider
budget. `test_provider_budget_enforced_fleet_wide` and
`test_100_page_job_completes_all_callbacks` are the guards.

**Disposition. REQUIRED FOR V1.**

---

### C-10. Where is state, and what if Postgres is unavailable at accept time?

**Concern.** You cannot honestly return 202 if you cannot persist the job. But if you return 503, you
have made your availability strictly worse than a fire-and-forget design.

**Verdict. REAL PROBLEM, with a deliberate and defensible answer.**

Face it squarely: **if Postgres is down, the API returns `503` and does not accept the job.** That is a
real availability reduction, and it is the correct trade. The alternative — 202 without persisting —
means the Backend stops retrying a job that will never run, and the book stalls **silently and
permanently**. A `503` makes the Backend retry (REQ-IDEM-03 says it does, with exponential backoff and
dead-lettering), which is exactly the behaviour we want.

The deeper point: a durable store is not an implementation choice here. REQ-IDEM-02 requires it in so
many words — *"verifies it has not any Prev one in db or any data store via GUID given"*. You cannot be
idempotent by GUID without somewhere to record the GUID.

**Resolution.** `503` + `EXTERNAL_SERVICE_ERROR`, tested by
`test_postgres_down_returns_503_not_202`. Managed Postgres with PITR. The whole dataset is **megabytes**
(~128 rows per book), so restore is fast.

**Disposition. REQUIRED FOR V1.** The honest alternative — broker ingestion, per
[C-03](#c-03-why-rest-the-backend-presumably-already-runs-a-broker-why-not-consume-from-it) — is not
available to us.

---

### C-11. Three layers are retrying. How do you stop that multiplying?

**Concern.** The Backend retries the job, you retry the operation, the SDK retries the call. 3 × 3 × 2 =
18 attempts for one logical action.

**Verdict. REAL PROBLEM — and it is already happening in the existing code.**
`walidapro/.../final_context_reviewer.py` retries 3× at the application level (`:807`, clamped 1..3 at
`:251`) while never overriding the OpenAI SDK's default `max_retries=2` (`:267`, `:825`). That is **up to
9 billed completions and ~30 minutes of worst-case latency for one page**, before the Backend's retry
makes it 27.

**Resolution.** Four rules in [09-async-jobs.md](09-async-jobs.md) §8: set every SDK's retry count
explicitly (usually 0); own retries at exactly one layer; prefer idempotency over aggression, because the
Backend re-delivers anyway; and never retry a non-retryable status —
`walidapro/ocr_verifier/lighton_ocr.py:180` currently retries auth failures identically to 429s.

**Disposition. REQUIRED FOR V1.**

---

### C-12. How do you guarantee idempotency when the *Backend's* endpoints may not be idempotent?

**Concern.** You retry `confirm-upload` and `completed`. If those are not idempotent on their side, your
retries corrupt their state — and you have no control over that.

**Verdict. PARTLY REAL. This is a genuine unresolved dependency, not a solved problem.**

What we control: four levels of our own idempotency
([09-async-jobs.md](09-async-jobs.md) §9), and a `one_callback_per_target` unique constraint that makes
enqueueing two `completed` callbacks for one page **structurally impossible**. The drainer never re-sends
a row it has seen acked.

What we do not control: the case where the Backend processed our PATCH but we never saw the 200. Then we
retry and they see a duplicate. **We cannot eliminate this** — at-least-once is the best achievable
without a two-phase protocol the PDF does not define.

**Resolution.** `Q-02` and `Q-03`, escalated to the Backend team as two of the most important questions
in the register. Meanwhile the fake backend can inject duplicate `confirm-upload` and duplicate
`completed` so we at least learn our own behaviour before they answer.

**Disposition. REQUIRED FOR V1** — ask the questions. The mechanism is already as good as it can be.

---

### C-13. How do you version a model, and how do you roll one back?

**Concern.** Model ids are hardcoded constants. Rolling back means a code change during an incident.

**Verdict. REAL PROBLEM in the current code.** Confirmed: `src/pipeline/ocr_gemini.py:28`,
`tts_gemini.py:16-17`, `tts_hume.py:12`, `tools/context_detector.py:46`,
`walidapro/.../final_context_reviewer.py:246`, `align_whisper.py:19-23`. Several are `-preview` models,
which *will* be deprecated.

**Resolution.** [ADR-008](19-adrs.md): every model id becomes config; provenance is stamped on every
inference log line and inside the timing artifact's `provenance` block; rollback is a config change plus
a worker restart.

One honest gap: **the `Image` and `Audio` artifacts are raw bytes with nowhere to record provenance.**
Their provenance exists only in our logs and in the sibling timing JSON. If someone asks "which TTS model
produced this WAV" six months from now, the answer is a log query, not the file.

**Disposition. REQUIRED FOR V1.**

---

### C-14. What happens when Gemini or LightOn fails, or deprecates a model?

**Concern.** Your whole quality story depends on two third-party APIs you do not control.

**Verdict. PARTLY REAL, and worse than the design admits in one respect.**

Handled: outage → retry → page `/failed` with `AI_INFERENCE_ERROR`. Rate limit → budget-aware retry.
Deprecation → config change ([ADR-008](19-adrs.md)). Garbage output → the existing validators catch it
(`_duration_is_sane`, the letters-unchanged guard, `_validate_result`).

**Not handled:** there is **no fallback engine wiring**. Both `OcrEngine` implementations exist behind one
port, so switching Gemini→LightOn is a config change — but it is a *manual* one, not an automatic
failover. A total Gemini outage means every page fails until a human changes config.

**Resolution.** Accept for V1; the port makes automatic failover a later addition rather than a
redesign. Note that automatic failover would be a *quality* change too — the two engines produce
different geometry (`box_level: "line"` versus `"none"`), so silently switching would silently change
the output contract.

**Disposition. FUTURE WORK.**

---

### C-15. What happens when the uploaded file is malicious or malformed?

**Concern.** You hand attacker-influenced bytes to `pypdfium2` and PyMuPDF — C libraries. That is a code
execution surface, and a decompression-bomb surface.

**Verdict. REAL PROBLEM, and adequately addressed.** [12-security.md](12-security.md) §6: streamed
download with a hard byte cap, magic-byte verification, a page-count ceiling, parse failure treated as
`SOURCE_INVALID` data rather than a crash, encrypted PDFs rejected, scratch files never executed or
served, and a download timeout.

The related and more likely attack is **SSRF via `sourceDownloadUrl`** — caller-supplied and fetched
server-side. Scheme allowlist, **host allowlist**, resolve-then-check to close the DNS-rebinding window,
and no redirect following. Marked **required for V1, not hardening**.

**Resolution.** Note what is *not* done: PDF parsing is not sandboxed in a separate process. Given the
caller is a trusted first party and the source comes from their own storage, that is proportionate for
V1.

**Disposition. REQUIRED FOR V1** (validation + SSRF). **FUTURE WORK** (sandboxed parsing).

---

### C-16. Where do large files live? What is the scratch high-water mark?

**Concern.** You are generating megabytes per page with no stated disk budget.

**Verdict. NOT A PROBLEM, and the numbers are measured.**
[11-data-and-storage.md](11-data-and-storage.md) §4: ~16 MB per concurrent page task typical, ~24 MB
worst; ~100 MB per worker host at default concurrency. Recommended volume 5 GB — ~50× headroom, sized for
an uncapped source PDF (`Q-08`) and the verified tier's unbounded crop writes
(`cropper.py:205` writes 7 PNGs *per suspicious word*).

**Resolution.** The one real hazard is `walidapro/ocr_verifier/pdf_render.py:41-107`, which materializes
every page as PIL RGB — ~1.4 GB for a 100-page book. `T-3.1` (streaming renderer) is a hard prerequisite,
guarded by `test_streaming_renderer_does_not_materialize_all_pages`.

**Disposition. REQUIRED FOR V1** (`T-3.1`).

---

### C-17. Are you introducing microservices unnecessarily? You have three container roles.

**Concern.** `api`, `worker`, `reconciler`. That is three things to deploy for one job queue.

**Verdict. PARTLY REAL on the reconciler.** The API/worker split is justified by measured blocking work.
The reconciler is a timer and two SQL queries — it does **not** need to be a separate container. It is
listed as one for operational clarity (its restarts and logs are separable), but it could equally be an
in-worker scheduled task guarded by an advisory lock.

**Resolution.** Either shape is fine; the document should not imply the separate container is required.
Note the important point: **all three roles are the same image with different entrypoints.** There is one
build, one test suite, one set of DTOs. This is not microservices; it is one service with three run
modes.

**Disposition. NO ACTION** — treat the reconciler's deployment shape as an operator preference.

---

### C-18. Which component becomes the bottleneck?

**Concern.** You have not load-tested anything.

**Verdict. NOT A PROBLEM — the answer is known and it is not a component.**

In order:

1. **Remote provider rate limits.** Not speculation: `README.md:491` records "Gemini free-tier capped at
   10 req/day per model" and Hume quota exhausted mid-book, leaving one book with 33 page records and
   **zero** audio.
2. **Worker CPU for forced alignment** — RTF ≈ 0.20 measured, single-threaded per page.
3. **The per-word diacritics loop**, if the verified tier is ever enabled: strictly serial, p90 of 69
   sequential VLM round trips per page.
4. Postgres as a queue — a long way behind.

**Resolution.** The provider budget (`T-5.7`) is the mitigation for #1, and it is fleet-wide rather than
per-process so that scaling workers without raising the budget correctly achieves nothing.

**Disposition. REQUIRED FOR V1** (`T-5.7`).

---

### C-19. What is the most expensive part of the architecture?

**Concern.** Where does the money go?

**Verdict. NOT A PROBLEM to answer; it is stark.**

Paid provider calls, and specifically **the verified tier's diacritization at one vision call per Arabic
word**. Measured over 186 real pages: median 35, mean 38.1, p90 69, max 137 words per page. That single
stage is ~55% of the verified tier's 65–200 calls per page, against the fast tier's total of 1–2.

Second is audio storage: WAV at ~1.0 MB/page median, 86% of everything we upload; `README.md:186-190`
puts 1,000 books at 36 GB versus 12 GB for MP3 (`Q-09`).

Infrastructure is nearly free: CPU-only workers, one small Postgres holding megabytes.

**Resolution.** `ENABLE_VERIFIED_TIER=false` as a hard kill-switch, `ai_engine_inference_calls_total` for
visibility (there is **no cost instrumentation today at all**), and a fleet-wide budget.

**Disposition. REQUIRED FOR V1.**

---

### C-20. What is the biggest single point of failure?

**Concern.** Name it.

**Verdict. NOT A PROBLEM to answer.** **Postgres.** If it is down: the API cannot accept (503), workers
cannot claim, the outbox cannot drain, the reconciler cannot sweep. The entire engine stops.

That is the accepted consequence of [ADR-005](19-adrs.md), and it is the right trade — see
[C-10](#c-10-where-is-state-and-what-if-postgres-is-unavailable-at-accept-time). The system degrades to
"the Backend keeps retrying", which is the correct failure mode: nothing is lost, only delayed.

The *second* SPOF is more interesting: **the Keycloak token endpoint**. Without a token, every outbound
Backend call is impossible — no uploads, no callbacks — even though jobs keep queueing. It is alerted at
severity "page" (`ai_engine_token_refresh_total{outcome="failure"} > 0`) for exactly that reason.

**Disposition. NO ACTION.**

---

### C-21. What is hardest to scale?

**Verdict. NOT A PROBLEM to answer, and the answer is uncomfortable.**

Hardest is the thing we do not control: **provider quota**. Adding workers does not add quota. That is
precisely why the budget is fleet-wide rather than per-process — so the incentive is to raise the budget,
not the replica count.

Second hardest is the **verified tier's serial per-word loop**. It cannot be scaled by adding workers
because it is serial *within* a page; it needs intra-page parallelism first (`T-8.1`). This is why
[ADR-009](19-adrs.md) makes parallelization a hard precondition of promoting the tier.

Everything else scales trivially: page tasks are independent, workers are stateless, and Postgres holds
megabytes.

**Disposition. NO ACTION.**

---

### C-22. What assumptions did you make that the PDF does not support?

**Verdict. NOT A PROBLEM — they are all labelled.** Five, in
[02-v1-requirements.md](02-v1-requirements.md) §9:

| | Assumption | Risk if wrong |
|---|---|---|
| A1 | `pageNumber` is 1-based | Every artifact attached to the wrong page |
| A2 | `pagesCount` is the full PDF page count | The Backend waits for pages we never produce |
| **A3** | **The `Text` artifact is final corrected + diacritized text** | **Both job scopes change** |
| A4 | `fileName` is ours to choose | Cosmetic |
| A5 | The presigned PUT needs no `Authorization` header | Uploads fail immediately and visibly |

A3 is the consequential one and it is `Q-04`, flagged as blocking integration.

Two further design choices that the PDF neither supports nor forbids, and are labelled PROPOSAL
throughout: the `qualityTier` field, and the `409` response for a same-`jobId`-different-body request.

**Disposition. REQUIRED FOR V1** — get `Q-04` and `Q-05` answered.

---

### C-23. Which requirements are still ambiguous?

**Verdict. NOT A PROBLEM to answer.** Sixteen, in [22-open-questions.md](22-open-questions.md). One
blocks implementation (`Q-01`, inbound token validation), two block integration (`Q-04` text semantics,
`Q-05` page indexing), nine should be answered before go-live, four can default.

The uncomfortable framing: **the PDF specifies no non-functional requirements whatsoever** — no latency,
throughput, availability, retention, size limit, concurrency limit, or security requirement beyond
bearer auth. Every one of those is our proposal.

**Disposition. REQUIRED FOR V1** — the three blocking ones.

---

### C-24. Is Postgres-as-a-queue a mistake?

**Concern.** It is the classic "we'll just use the database" decision that everyone regrets. Long
transactions bloat, `SKIP LOCKED` contends, and you end up building a broker badly.

**Verdict. PARTLY REAL as a general criticism, NOT A PROBLEM at this scale — and the reasoning is
unusual enough to be worth restating.**

The decisive point is that **the durable store is not optional.** REQ-IDEM-02 requires a store keyed by
job GUID in so many words. Given we must run Postgres anyway, a broker would be a *second* stateful
dependency and a second failure mode — for a queueing need that is measured in **books per day**.

The workload numbers: ~128 rows per book, ~13 K rows/day at 100 books/day, ~4.7 M rows/year — megabytes.
Claim rate is a handful per minute. This is three or four orders of magnitude below where
Postgres-as-a-queue gets uncomfortable.

**Resolution.** The criticism is right about the general case and wrong about this one. What matters is
that the reversal condition is **concrete and pre-committed**: sustained enqueue > ~50 jobs/s, claim-query
p99 > 50 ms, or a need for priority classes. The `JobStore` port isolates the change to one adapter.

**Disposition. NO ACTION**, with the trigger documented in [ADR-004](19-adrs.md).

---

### C-25. Is the reconciler a band-aid over a design flaw?

**Concern.** If your callback delivery were correct, you would not need a background process sweeping for
work you forgot to report. The reconciler is an admission that the primary path is unreliable.

**Verdict. PARTLY REAL. This is the fairest criticism in the review and it is conceded in part.**

The honest position: the outbox handles "we decided to report and then died". The reconciler handles "we
never got as far as deciding" — a SIGKILL mid-inference leaves a `RUNNING` `job_pages` row with **no**
outbox entry and therefore nothing to drain. That is not a flaw in the outbox; it is a case the outbox
categorically cannot cover, because the intent was never formed.

So the reconciler is not compensating for an unreliable primary path. It is covering process death, which
no in-process mechanism can cover from the inside.

**Where the criticism lands:** there is real overlap. Lease expiry already returns an abandoned page to
`PENDING`, so the page *will* be retried without the reconciler. The reconciler's genuinely unique job is
narrower than the design implies — it is the **deadline enforcer**, converting "retried forever" into
"reported failed once". That is a smaller and more honest description than "INVARIANT-1 enforcer".

**Resolution.** Keep it, and describe it accurately. Its metric —
`ai_engine_pages_total{outcome="force_failed"}` — should be **zero in normal operation**, and it is
alerted on precisely so that a non-zero value is treated as "a worker died", not as "the system working
as intended".

**Disposition. REQUIRED FOR V1**, with the description tightened.

---

### C-26. Is 21.8% interpolated timing good enough to ship?

**Concern.** One word in five has a *guessed* timestamp. For a karaoke reader teaching children to read,
a highlight that drifts off the spoken word is worse than no highlight at all.

**Verdict. REAL PROBLEM. Do not let this one pass.**

The fleet average of 78.0% anchored hides the distribution. Per book, `README.md:473,491` records:

| Book | Engine | Anchored | Interpolated |
|---|---|---|---|
| النجوم | Hume native word timestamps | **93.7%** (759/810) | — |
| المومياوات | Gemini TTS + faster-whisper forced alignment | **51.0%** (348/682) | 47.8% (326) |

**Nearly half the timings on the Gemini path are interpolated.** And interpolation is not sophisticated:
`src/pipeline/speak.py:64 _interpolate` divides the gap between anchors in proportion to letter count.
That is a reasonable heuristic and it is not phonetics.

**Resolution — partial, and honestly partial:**

1. `t_src` is per token, so the reader **can** style interpolated words differently or decline to
   highlight them. That is a product decision we can enable but not make.
2. `provenance.anchor_ratio` ships **inside** the artifact, so a consumer can see per-page quality.
3. `ai_engine_alignment_anchor_ratio` alerts on fleet regression.
4. `T-8.7` evaluates Hume native timestamps as the default — measurably 93.7% versus 51.0%.

**The root cause is not fixable by us:** Gemini TTS has **no timestamp API at all**
(`src/pipeline/tts_gemini.py`), so forced alignment is the only option on that path. Improving this means
changing TTS vendor, or accepting the vendor's quota limits (Hume's was exhausted historically), or
investing in better forced alignment.

**Disposition. REQUIRED FOR V1 — as a product conversation, not a code change.** This must be shown to
product before V1 is accepted. It is `R-03`, one of only two risks with medium residual risk after
mitigation.

---

### C-27. Is the "fast tier" default a quality regression the product will reject?

**Concern.** You are shipping the weaker of two engines you already own, to save money on a product whose
entire value is reading Arabic correctly to children.

**Verdict. REAL PROBLEM — and the document set says so rather than hiding it.** `R-12`, and
[ADR-009](19-adrs.md) argues it at length.

The three facts that force the decision:

1. **Pipeline W cannot produce the mandated timing artifact.** Zero hits for
   `timing|timestamp|word_time|srt|vtt` across all `walidapro/**/*.py`. Job B is impossible on it. This
   is not a preference; REQ-ART-04 is unsatisfiable on that path.
2. **40–100× the cost**, dominated by one vision call per word.
3. **Zero tests across 16,155 LOC**, and not even collectible by pytest.

Making the first integration simultaneously the most expensive, the slowest, and the least tested thing
in the system maximizes the chance that the first joint test fails for reasons unrelated to the protocol
— which is the actual unknown.

**Resolution.** The honest answer is that this is `Q-07`'s to decide, and `Q-07` is unanswered. If
acceptance is on AI accuracy, V1 as designed will be judged inadequate and two prerequisite projects
(`T-8.1` parallelization, `T-8.2` tests) come first. **Ask product before building, not after.**

**Disposition. REQUIRED FOR V1** — resolve `Q-07`.

---

### C-28. Why keep two pipelines at all? Delete one.

**Concern.** Two implementations of the same thing is the biggest structural problem in the repository,
and every day it persists the divergence grows.

**Verdict. PARTLY REAL. The criticism is correct; the timing is not.**

Neither is a superset of the other, which is what makes deletion unsafe:

- Pipeline S has the only timing capability, the free text-layer path (25 of 36 sample books), and all
  56 tests.
- Pipeline W has the blind-crop protocol, the letters-never-change guard, the `UnsafeReviewError`
  envelope, and per-page failure isolation.

Deleting W destroys correctness machinery the product will want. Deleting S makes the mandated timing
artifact impossible. Merging them is a 16,155-LOC exercise with zero tests on one side.

**Resolution.** `T-8.8` consolidates, **after** V1 ships and after `T-8.2` gives `walidapro/` a test
suite. Doing a large merge with observability and a test suite in place is much safer than doing it
first. `R-08` records that the divergence keeps accruing in the meantime — that cost is real and
accepted, not dismissed.

**Disposition. FUTURE WORK**, with the debt explicitly booked.

---

### C-29. What happens when the presigned upload URL expires mid-upload?

**Concern.** A 8.9 MB WAV on a slow link, and the ticket expires halfway through the PUT.

**Verdict. NOT A PROBLEM, and it is specified.** A `403` on a presigned PUT is treated as **expired**
rather than forbidden, which triggers one fresh `initialize-upload` for a new ticket — at most once, with a
loop guard. Same asymmetric treatment as the download path (REQ-SRC-02 exists precisely because presigned
URLs expire).

**Resolution.** The TTL is `Q-08`, so we cannot yet size the retry window against it. Note the cost: a
fresh ticket means a second `fileId` and an orphaned first one (`R-15`, `Q-02`).

**Disposition. REQUIRED FOR V1** (the behaviour), **`Q-08`** (the sizing).

---

### C-30. What if `pagesCount` changes after you have PATCHed it?

**Concern.** You count pages, report 30, then discover the PDF is really 31 — or a page fails to render
and you decide it is not a page.

**Verdict. NOT A PROBLEM, because `pagesCount` is a property of the file, not of our processing.**
`page_count()` reads `len(doc)` once from the parsed PDF. It cannot change unless the file changes, and
the file is immutable behind a `fileId`.

The important discipline: **a page that fails to render is still a page.** It gets a `job_pages` row and
a `/failed` callback. Silently reducing `pagesCount` because a page was hard would strand the Backend
waiting for a page we quietly decided did not exist — the exact failure REQ-META-02 warns about.

**Resolution.** `pagesCount` is written once (`jobs.pages_count`) and the ledger is generated from it
in one statement, so there is no code path that can disagree with itself.

**Disposition. NO ACTION.**

---

### C-31. Is the outbox over-engineering for V1?

**Concern.** A transactional outbox is a pattern for high-reliability distributed systems. This is a
job runner for children's books.

**Verdict. NOT A PROBLEM — and the justification is a specific, short window rather than a general
principle.**

Consider *upload image → upload text → send `completed`*. If the callback is a bare in-line HTTP call
and the worker dies between the last `confirm-upload` and the PATCH, then: the artifacts exist in the
Backend's storage, the page is genuinely done, and **nothing will ever tell the Backend so.** The lease
expires and the page is retried — but a retry that finds both artifacts already confirmed may reasonably
conclude the page is done and never send the callback. The book stalls forever.

That window is a few hundred milliseconds long and it occurs on **every deploy**. The outbox removes it
entirely by making the intent durable before the process can die.

**Resolution.** The implementation is small: one table, one unique constraint, one drainer loop. The
`one_callback_per_target` constraint additionally makes a double `completed` for one page structurally
impossible — which is worth having independent of crash safety.

**Disposition. REQUIRED FOR V1.**

---

### C-32. What breaks if V2 doubles the traffic?

**Verdict. NOT A PROBLEM.** Doubling books/day: add worker replicas — but **check the provider budget
first**, because that is the actual ceiling. Postgres at 2× is still megabytes. The API is untouched.

What would need attention at **10×**: Postgres as a queue ([ADR-004](19-adrs.md)'s trigger), and the
provider spend, which scales linearly with pages and is the real constraint.

**Disposition. NO ACTION.**

---

### C-33. What breaks if you replace the AI model?

**Verdict. PARTLY REAL.** Config change and a worker restart for the *mechanics*
([ADR-008](19-adrs.md)). But two things genuinely break:

1. **The geometry contract.** Gemini OCR gives `box_level: "line"` (subdivided RTL by letter count at
   `src/pipeline/ocr_gemini.py:87`); LightOn gives `box_level: "none"`. Switching engines silently
   changes whether boxes exist at all — which matters if `Q-10` turns out to need them.
2. **Timing quality.** Switching TTS engine changes `anchor_ratio` dramatically — 93.7% on Hume native
   versus 51.0% on Gemini+whisper. A "harmless" TTS swap would halve the karaoke quality.

**Resolution.** Both are visible: `box_level` is in the page record, and
`ai_engine_alignment_anchor_ratio` is labelled by `tts_model`, so the before/after comparison is one
query. But **visible is not the same as safe** — a model change needs a deliberate quality check, not
just a config commit.

**Disposition. REQUIRED FOR V1** — the labelled metric. **FUTURE WORK** — a model-change checklist.

---

### C-34. Which APIs will be painful to maintain?

**Verdict. NOT A PROBLEM to answer honestly.**

- `POST /api/jobs` — fine. One endpoint, a generated schema, additive-only evolution.
- **The timing JSON artifact** — this is the one. It is a schema we invented (`Q-10`), consumed by a
  reader app we have not seen, with no versioning agreement in place. It is also the artifact most likely
  to need new fields (boxes, sentence boundaries, per-token confidence). Hence `schema_version` from day
  one and an explicit "consumers must ignore unknown fields" rule.
- **The outbound Backend contract** — painful because we do not own it. A change on their side breaks us
  silently, and we have no contract test against their real service. The nightly opt-in suite partly
  covers this.
- `GET /api/jobs/{jobId}` — deliberately marked optional so it can be changed or dropped freely.

**Disposition. REQUIRED FOR V1** — `schema_version` and the unknown-field rule.

---

### C-35. What is the single thing most likely to make the first integration test fail?

**Verdict. NOT A PROBLEM to answer, and worth answering bluntly.**

**`Q-01` — inbound token validation.** Every other piece can be built and verified offline against the
contract fake. Authentication is the only part whose correctness depends on a fact we do not have: which
Keycloak client the Backend presents and what claims to expect. If the answer arrives on the day of the
test, the test fails on a 401 and everyone spends the session debugging auth instead of the protocol.

Second most likely: **`SOURCE_URL_ALLOWED_HOSTS` unset or wrong**, which rejects every job with a
validation error that looks like a contract mismatch (`R-22` is the sibling failure —
`LIGHTON_BASE_URL` misconfigured as shipped).

Third: `pageNumber` indexing (`Q-05`), which would produce a full run whose artifacts are all attached
one page off — the worst kind of failure, because it *looks* like success.

**Resolution.** `Q-01` and `Q-05` are asked now; the pre-flight checklist in
[18-deployment.md](18-deployment.md) §9 lists both as prerequisites, and the smoke sequence tests
authentication before anything else.

**Disposition. REQUIRED FOR V1.**

---

## What I would still push back on

Three points survive this review and should not be treated as settled.

### 1. Timing quality may not be good enough for the product ([C-26](#c-26-is-218-interpolated-timing-good-enough-to-ship))

Nearly half the timings on the Gemini path are interpolated by letter-count proportion, not measured.
The fleet average of 78% conceals a 51%-to-94% per-book range. For a reading-education product whose
core interaction is word-level highlighting, this is the weakest part of the delivery, and it is a
model-capability limit rather than a bug we can fix. **It must be demonstrated to product before V1 is
accepted**, not discovered in a review of the shipped reader.

### 2. The fast-tier default is an unresolved product decision, not an engineering conclusion ([C-27](#c-27-is-the-fast-tier-default-a-quality-regression-the-product-will-reject))

[ADR-009](19-adrs.md) is well-argued, and Pipeline W's inability to produce the timing artifact makes it
unavoidable for Job B. But for **Job A** the choice is genuinely contestable, and it rests entirely on
`Q-07` — which nobody has answered. Building on the assumption that V1 is accepted on protocol
conformance is reasonable; **shipping** on that assumption without confirming it is not.

### 3. Two pipelines is a structural problem V1 does not fix ([C-28](#c-28-why-keep-two-pipelines-at-all-delete-one))

Deferring consolidation to `T-8.8` is the right sequencing — a 16,155-LOC merge is safer with tests and
observability in place. But the debt accrues, and 83% of production Python remains untested throughout
V1. If `T-8.2` (a `walidapro/` test suite) slips, this becomes the repository's dominant risk rather than
a booked one.
