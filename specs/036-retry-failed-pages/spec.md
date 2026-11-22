# Feature Specification: A page that fails for a temporary reason is tried again before it is reported failed

**Feature:** `036-retry-failed-pages` · **Created:** 2026-11-22 · **Status:** Draft · **Epic:** 004
**Priority:** P2

## Problem

The design says a page that hits a *retryable* error goes back to `PENDING` with backoff and is
tried again, up to `PAGE_MAX_ATTEMPTS` (3), before `/failed` is sent
(`docs/backend-v1/14-error-model.md` §1 table and §4 flow, `docs/backend-v1/09-async-jobs.md:90`
and `:286`). The service does not do this:

- `PageTaskExecutor.run` (`service/ai_engine/application/run_page_task.py:150-190`) sends every
  caught `AiEngineError` and every page timeout straight to `_report_failed`, so the page becomes
  `REPORTED_FAILED` on its first attempt. Job B (`run_audio_job.py:108-155`) does the same.
- `settings.page_max_attempts` (`config/settings.py:143`) is never read.
- `ErrorCode.retryable` / `AiEngineError.retryable` (`domain/errors.py:168`, `:192`) is never
  read outside `errors.py` and tests. Spec 007 recorded this (`007…/tasks.md:14`).
- `PageTask.attempt` never moves in production: nothing calls `claim_page`, and nothing sets a
  page to `RUNNING`. Only `claim_page` (used by tests alone) increments it.

Per-call HTTP retries (`adapters/http/retry.py`, up to 5 attempts in a 120 s budget) absorb short
blips. A provider outage longer than that, a model that fails to load once, or one slow page that
hits the 900 s page timeout, fails the page **permanently** after a single attempt. The Backend
is then the only thing that can retry, by sending a new job, which repeats work the engine has
already done and is the stacked-retry risk `R-04` wanted to avoid.

The Backend contract is not affected: `/failed` carries only `{reason}`
(`02-v1-requirements.md` REQ-CB-04/05, REQ-ERR-01). `retryable` is never on the wire, so this is
an internal behaviour change.

## User Story 1 — A temporary provider failure does not fail the page (P2)

**Given** a Job A page whose OCR call raises `AiInferenceError` on its first attempt and succeeds
on its second, **When** the job runs, **Then** the page is reported **completed**, exactly once,
and no `/failed` callback is ever sent for it.

**Acceptance:**
1. First attempt fails `AI_INFERENCE_ERROR` → page stays `PENDING`, no callback, job deferred by
   the backoff delay. The next run of the job completes the page → one `/completed`.
2. Artifacts already confirmed by the failed attempt are not uploaded again (artifact ledger).
3. Pages that already reported keep their reports. Only the failed page is run again.

## User Story 2 — A page that keeps failing is still reported failed, once (P2)

**Given** a page that fails with a retryable code on every attempt, **When** it has been tried
`PAGE_MAX_ATTEMPTS` times, **Then** exactly one `/failed` is sent, with the code of the last
failure, and the page is `REPORTED_FAILED`. INVARIANT-1 holds.

**Acceptance:**
1. `PAGE_MAX_ATTEMPTS=3`, three `AI_INFERENCE_ERROR`s → 3 attempts, 1 `/failed`
   (`AI_INFERENCE_ERROR: …`), 0 `/completed`.
2. The attempt count survives a worker restart. A page that failed twice before the restart gets
   one more attempt after it, not three.
3. `PAGE_MAX_ATTEMPTS=1` restores today's behaviour exactly: first failure → `/failed`.

## User Story 3 — Errors a retry cannot fix are not retried (P2)

**Given** a page fails with a non-retryable code, or with a programming error, **When** the
page task handles it, **Then** it is reported failed on that attempt, as today.

**Acceptance:**
1. `SOURCE_INVALID`, `VALIDATION_ERROR` and the other codes outside `_RETRYABLE` → 1 attempt.
2. A programming error (spec 007, `PROGRAMMING_ERRORS`) and an unexpected `BaseException` →
   `INTERNAL_ERROR`, 1 attempt. A bug fails the same way every time, and retrying it would repeat
   paid calls for nothing.
3. `PROCESSING_TIMEOUT` is retried **once** (at most 2 attempts), per the error-model table.
4. `RATE_LIMITED` keeps its existing path: the page is parked, not counted as an attempt
   (a quota wall is not a failure of the page).

## User Story 4 — Job B retries its one page the same way (P3)

**Given** a Job B (audio) job whose TTS call fails with a retryable code, **When** the job runs,
**Then** the page is retried with the same limits and backoff before `/failed` is sent.

## Requirements

- **FR-001** One domain rule decides a failed attempt: `next_page_attempt(reason, attempts_made,
  max_attempts) → delay seconds | None` in `service/ai_engine/domain/`. `None` means "report
  failed now". Retry only when `reason.retryable`, `attempts_made < limit(code)`, and the code is
  not `RATE_LIMITED`. `limit(PROCESSING_TIMEOUT) = min(2, max_attempts)`, otherwise `max_attempts`.
- **FR-002** Backoff is exponential with full jitter: `uniform(0, min(cap, base · 2^(n-1)))`,
  base 5 s, cap 120 s (`09-async-jobs.md:286`). Base and cap are settings. The random source is
  injectable so tests are deterministic.
- **FR-003** `INTERNAL_ERROR` leaves `_RETRYABLE`. Spec 007 kept it there only because nothing
  read the flag. This spec adds the first reader, and retrying a bug is wrong (User Story 3).
  Update the error-model table row to "No (engine) · Backend may resubmit".
- **FR-004** The failed-attempt count is persisted per page and survives restarts. A new job-store
  operation records a failed attempt atomically: it increments `attempt`, stores the last
  `failure_code` / `failure_reason`, keeps the page `PENDING`, refuses a terminal page, and
  returns the new count. Both stores (`InMemoryJobStore`, `SqliteJobStore`) implement it. The
  existing `job_pages.attempt`, `failure_code` and `failure_reason` columns hold it, so no
  schema migration.
- **FR-005** A retried page ends its attempt like a parked page: no callback, page `PENDING`,
  `PageOutcome` carries the error code and the backoff as `retry_after_seconds`. The job is
  deferred by the largest `retry_after_seconds` of its non-terminal pages (the existing
  `defer_seconds` path in `run_extraction_job.py:171-187`).
- **FR-006** Logs and metrics tell retries apart from quota parking. Event `page.retry_scheduled`
  with `error_code`, `attempt`, `max_attempts`, `retry_after_seconds`.
  `ai_engine_pages_total{outcome="retried"}` counts each scheduled retry. The `job.deferred` log
  reports `retrying_pages` and `parked_pages` separately. A retry never sets the job's
  `error_code` to `RATE_LIMITED`.
- **FR-007** Job B applies FR-001 to FR-006 to its one page.
- **FR-008** Docs match the code: `service/docs/03-configuration.md` (`PAGE_MAX_ATTEMPTS`, backoff
  settings), `14-error-model.md` (FR-003 row), and a note in `007…/tasks.md` that its "nothing
  reads `.retryable`" line is superseded.

## Out of scope

- Per-code limits other than `PROCESSING_TIMEOUT`. The table's "UPLOAD_FAILED (5)" and
  "SOURCE_UNAVAILABLE (2 + 1 refresh)" are per-call budgets that `adapters/http/retry.py` and the
  source fetcher already own.
- Job-level retry (`JOB_MAX_ATTEMPTS` is also unread). Separate spec if wanted.
- Retrying `AUTHENTICATION_ERROR` from an unreachable token endpoint
  (`06-service-boundaries.md:282` calls it retryable, the enum does not).

## Cost (constitution VII)

A retry repeats a page's paid calls only for pages that failed, never on the happy path. The
worst case is a page that fails every time: `PAGE_MAX_ATTEMPTS` × its per-attempt calls (fast
tier 1–2 calls → at most 6). Stages that finished before the failure are served from the provider
cache (spec 021) at no cost, and every retried call still passes the provider budget, so a
fleet-wide allowance caps the total. `PAGE_MAX_ATTEMPTS=1` turns the feature off.

## Timing

Worst case per page: 3 attempts × 900 s timeout + backoff (≤ 120 s + 120 s) ≈ 47 min, well inside
`JOB_DEADLINE_SECONDS` (86,400 s). The reconciler stays the backstop. A page must never be
force-failed because of retries, and SC-003 checks that.

## Success Criteria

- **SC-001** Fault matrix: a page that fails once with a retryable code and then succeeds →
  exactly one `/completed`, zero `/failed`, for Job A and Job B.
- **SC-002** A page that always fails a retryable code → exactly `PAGE_MAX_ATTEMPTS` attempts and
  one `/failed`. A non-retryable code or a programming error → one attempt.
- **SC-003** `ai_engine_pages_total{outcome="force_failed"}` stays 0 in every new test.
- **SC-004** The whole service suite is green, with `PAGE_MAX_ATTEMPTS=1` reproducing today's
  fault-matrix results.

## Open questions

1. **Default `PAGE_MAX_ATTEMPTS`: 3 (the design) or 1 (off until measured)?** This draft keeps 3,
   since retries cost only on failed pages and are budget-metered. Choose 1 if the first rollout
   should be opt-in.
2. **Does deferring the whole job for one page's backoff matter?** Other non-terminal pages wait
   with it. They are already parked on the same path today, and backoff is ≤ 120 s, so this draft
   accepts it.
