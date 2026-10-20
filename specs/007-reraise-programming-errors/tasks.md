# Tasks: re-raise programming errors

- [ ] T001 [P] Test: `classify(TypeError())` → non-retryable internal — `test_retry.py`
- [ ] T002 [P] Test: fault-matrix row, injected `TypeError` in TTS adapter → 0 retries, 1 failed callback, code internal
- [ ] T003 `PROGRAMMING_ERRORS` tuple + internal code in `domain/errors.py`
- [ ] T004 `retry.classify` checks it first
- [ ] T005 `run_page_task` maps it
- [ ] T006 Apply to `src/pipeline` drivers together with 008
