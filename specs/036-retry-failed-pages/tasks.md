# Tasks: retry failed pages

Test-first: each test task lands red before its implementation task. All tests are hermetic
(fakes, injected clock and jitter), so they cost nothing to run.

## Test plan

### Unit: domain rule (`service/tests/unit/test_page_retry.py`, new)

- [ ] T001 [P] `next_page_attempt`:
  - a retryable code with attempts left → a delay. With attempts used up → `None`
  - every code outside `_RETRYABLE` → `None` on the first attempt (parametrised over the taxonomy)
  - `INTERNAL_ERROR` → `None` (FR-003)
  - `RATE_LIMITED` → `None` (parking owns it, FR-001)
  - `PROCESSING_TIMEOUT` → retried once: attempt 1 → delay, attempt 2 → `None`, even with `max_attempts=3`
  - `max_attempts=1` → `None` for every code (feature off)
  - backoff: with a fixed rng, delay = `rng · min(cap, base · 2^(n-1))`. Never above the cap. Never negative
- [ ] T002 [P] `errors.py`: `INTERNAL_ERROR.retryable is False`. The other 14 codes keep their current value (pins the taxonomy so FR-003 is the only change)

### Unit: store (`service/tests/unit/test_job_store.py`, `test_sql_store.py`)

- [ ] T003 [P] `record_failed_attempt`, run against both stores (existing `store` fixture parametrisation):
  - increments `attempt` by one per call and returns the new count
  - stores `failure_code` and `failure_reason` and leaves the page `PENDING`
  - refuses a `REPORTED_COMPLETED` or `REPORTED_FAILED` page (no state change)
  - concurrent calls on one page lose no increment (`asyncio.gather` of 5 → `attempt == 5`)
- [ ] T004 [P] SQL only: the count survives closing and reopening the store (the `test_a_job_survives_closing_and_reopening_the_store` pattern)

### Unit: executor (`service/tests/unit/test_page_task_retry.py`, new, `InMemoryJobStore` + fakes)

- [ ] T005 [P] Job A `PageTaskExecutor.run`:
  - `AiInferenceError` on attempt 1 → outcome `PENDING`, `retrying`, `retry_after_seconds > 0`, no callback enqueued, `attempt == 1`
  - same error with `attempt` already at `max - 1` → `REPORTED_FAILED`, one `PAGE_FAILED` callback with the last code
  - `TimeoutError` (page deadline) → retried once, then failed
  - `TypeError` (programming error) → failed immediately, `attempt` not incremented
  - `RateLimitedError` → parked exactly as today (`parked`, not `retrying`, `attempt` not incremented)
  - metrics: `PAGES_TOTAL{outcome="retried"}` +1 per scheduled retry, `failed` only on the final report
  - log: `page.retry_scheduled` carries `error_code`, `attempt`, `max_attempts`, `retry_after_seconds`
- [ ] T006 [P] Job B `RunAudioJob`: the same five cases for its single page

### End to end (`service/tests/e2e/`, `FakeBackend`)

- [ ] T007 [P] `test_page_retry.py` (new), Job A:
  - flaky OCR (fails once, then succeeds) on page 2 of 3 → pages 1 and 3 complete on the first run, the job is deferred, the second run completes page 2. Totals: 3 `/completed`, 0 `/failed`, 0 force-failed
  - always-failing OCR on page 2 → exactly `PAGE_MAX_ATTEMPTS` runs of page 2, 1 `/failed` (`AI_INFERENCE_ERROR: …`), pages 1 and 3 completed once each
  - artifacts confirmed before the failure are not uploaded again (`fake.artifacts_for(page)` lists each artifact type once)
  - restart between attempts (new store instance on the same SQLite file) → total attempts still capped at `PAGE_MAX_ATTEMPTS`
- [ ] T008 [P] Same file, Job B: flaky TTS → 1 `/completed`. Always-failing TTS → 1 `/failed` after `PAGE_MAX_ATTEMPTS`
- [ ] T009 [P] `test_fault_matrix.py`: add a row "transient provider error, recovers on retry". INVARIANT-1 check passes for every existing fault with `page_max_attempts` at 1 and at 3 (parametrise)
- [ ] T010 [P] A deferred-for-retry job sends exactly one metadata callback across all its runs, and the reconciler never force-fails a retrying page inside the deadline

## Implementation

- [ ] T011 Adjust the existing failure-path tests that rely on first failure = `/failed` to run with `page_max_attempts=1`, and keep them asserting today's behaviour (`test_fault_matrix.py:236-271`, `test_job_a_failure_paths.py`, `test_job_b_end_to_end.py`). Do this before T014, so the suite shows only the new tests red
- [ ] T012 `domain/page_retry.py` + `_RETRYABLE` change (makes T001, T002 green)
- [ ] T013 `record_failed_attempt` in port, memory and SQL stores (T003, T004)
- [ ] T014 `PageTaskExecutor._fail_attempt`, `PageOutcome.retrying`, metrics and log (T005)
- [ ] T015 `run_extraction_job` defers on parked or retrying. Separate counts in `job.deferred`. Fix the job `error_code` (T007, T009, T010)
- [ ] T016 `RunAudioJob._fail_attempt` (T006, T008)
- [ ] T017 Settings `page_retry_base_seconds`, `page_retry_max_delay_seconds`, wiring in `worker.py`. `test_config.py` case for the defaults and for env overrides
- [ ] T018 Docs: `service/docs/03-configuration.md`, `docs/backend-v1/14-error-model.md` `INTERNAL_ERROR` row, superseded note in `specs/007-reraise-programming-errors/tasks.md`, row 036 in `specs/README.md`

## Verification

- [ ] T019 `cd service && uv run pytest` (whole suite, including slow) green. `ruff check` and `ruff format --check` clean. Record pass counts here, as in spec 007
- [ ] T020 Grep check: `page_max_attempts` and `.retryable` each have a production reader. `claim_page` is still test-only, and that is noted in the store port docstring
