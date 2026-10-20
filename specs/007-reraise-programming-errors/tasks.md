# Tasks: re-raise programming errors

- [ ] T001 [P] Test: `classify(TypeError())` → non-retryable internal — `test_retry.py`
- [ ] T002 [P] Test: fault-matrix row, injected `TypeError` in TTS adapter → 0 retries, 1 failed callback, code internal
- [ ] T003 `PROGRAMMING_ERRORS` tuple + internal code in `domain/errors.py`
- [ ] T004 `retry.classify` checks it first
- [ ] T005 `run_page_task` maps it
- [x] T006 Applied to `src/pipeline` drivers together with 008: `failures.PROGRAMMING_ERRORS = (NameError, AttributeError, TypeError)`, the same set walidapro uses. `KeyError` is deliberately not included on the CLI side, because a malformed provider JSON response raises it and that is a page failure, not a bug. Revisit for the service side.
