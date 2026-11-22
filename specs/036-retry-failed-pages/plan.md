# Plan: retry failed pages

**Spec:** [spec.md](spec.md)

## Approach

1. **Domain rule** (`service/ai_engine/domain/page_retry.py`, stdlib only):
   `next_page_attempt(reason, attempts_made, max_attempts, *, base, cap, rng) -> float | None`
   and `page_attempt_limit(code, max_attempts) -> int`. All of FR-001/FR-002 lives here, so the
   rule is unit-tested without a store or an executor.
2. **Taxonomy:** remove `ErrorCode.INTERNAL_ERROR` from `_RETRYABLE` (`domain/errors.py`).
3. **Store port** (`ports/job_store.py`): add
   `record_failed_attempt(job_id, page_number, reason) -> PageTask`. It increments `attempt`,
   stores `failure_code` / `failure_reason`, and keeps the page `PENDING`. It raises the store's
   existing terminal-page refusal if the page is already terminal. Implement it in
   `adapters/persistence/memory.py` and `adapters/persistence/sql.py` (one `UPDATE … WHERE state
   NOT IN terminal`, the same pattern as `set_page_state`). No schema change.
4. **Job A executor** (`application/run_page_task.py`): split the `except` arms in `run()`:
   - `RateLimitedError` → `_park` (unchanged).
   - `TimeoutError`, `ProcessingTimeoutError`, `AiEngineError` → `_fail_attempt(job_id, page,
     reason)`, which calls `record_failed_attempt`, asks `next_page_attempt`, then either returns
     a `PENDING` outcome with `retry_after_seconds=delay` (log `page.retry_scheduled`,
     `PAGES_TOTAL{outcome="retried"}`) or calls `_report_failed`.
   - `PROGRAMMING_ERRORS`, `BaseException` → `_report_failed` directly (unchanged). Not counted
     as an attempt.
   - `PageOutcome` gets a `retrying` property (`PENDING` and `error_code` is not `RATE_LIMITED`).
     `parked` keeps its meaning for the quota case.
5. **Job A orchestrator** (`application/run_extraction_job.py:171-187`): defer on any
   non-terminal outcome. Log `parked_pages` and `retrying_pages` separately. The job's
   `error_code` is `RATE_LIMITED` only if some page was parked for quota, otherwise the last
   retry's code.
6. **Job B** (`application/run_audio_job.py:108-155`): the same `_fail_attempt` split. Its
   existing park path already defers the job (`defer_seconds`), so a retry reuses it.
7. **Wiring** (`worker.py`): pass `settings.page_max_attempts`,
   `settings.page_retry_base_seconds` (new, 5.0) and `settings.page_retry_max_delay_seconds` (new,
   120.0) into both executors. Add the two new settings to `config/settings.py` and
   `service/docs/03-configuration.md`.
8. **Docs:** apply FR-003 to the error-model row. Add a superseded note in
   `specs/007-reraise-programming-errors/tasks.md`. Add a row 036 in `specs/README.md`.

## Why reuse the park path

Parking already proves the hard part. A page can end an attempt with no callback, stay
`PENDING`, defer its job, be picked up again on the next claim, skip confirmed artifacts, and
still be caught by the reconciler deadline. A retry is the same shape with a different reason
and a counter. No new state, no new loop, no new scheduler.

## Constitution check

- **V (per-page isolation):** still exactly one terminal report per page. The retry ends an
  attempt, not the page. The attempt count is persisted, so a restart costs at most the page in
  flight.
- **VII (cost):** extra paid calls only on failing pages, bounded by `PAGE_MAX_ATTEMPTS`, cached
  stages free, budget-metered. `PAGE_MAX_ATTEMPTS=1` turns it off.
- **VIII (test without paying):** every test uses fakes. The jitter source is injected.
- **IX (layering):** the rule is in `domain/` (stdlib), the decision is in `application/`,
  persistence is in adapters.
- **X (honest reporting):** retries get their own log event and metric outcome. They are not
  reported as quota parking.

## Files

```
service/ai_engine/domain/page_retry.py                 new: rule + backoff
service/ai_engine/domain/errors.py                     INTERNAL_ERROR out of _RETRYABLE
service/ai_engine/ports/job_store.py                   record_failed_attempt
service/ai_engine/adapters/persistence/memory.py       record_failed_attempt
service/ai_engine/adapters/persistence/sql.py          record_failed_attempt
service/ai_engine/application/run_page_task.py         _fail_attempt, PageOutcome.retrying
service/ai_engine/application/run_extraction_job.py    defer on parked or retrying
service/ai_engine/application/run_audio_job.py         _fail_attempt
service/ai_engine/config/settings.py                   page_retry_base_seconds, page_retry_max_delay_seconds
service/ai_engine/worker.py                            wiring
service/docs/03-configuration.md                       settings
docs/backend-v1/14-error-model.md                      INTERNAL_ERROR row
specs/007-reraise-programming-errors/tasks.md          superseded note
specs/README.md                                        row 036
```

## Risks

- **Existing tests assume first failure = `/failed`.** The fault matrix and the Job A/B failure
  paths (`tests/e2e/test_fault_matrix.py:236-271`, `test_job_a_failure_paths.py`,
  `test_job_b_end_to_end.py`) will see `PENDING` instead. T011 runs them with
  `page_max_attempts=1` where the old behaviour is what they test, and adds retry variants next
  to them. No assertion is deleted.
- **A deferred job re-runs its whole preamble** (source download, page count, metadata gate) on
  the next claim. That is free and idempotent today (outbox dedupe, metadata gate), and parking
  already relies on it. T010 checks that no second metadata callback goes out.
