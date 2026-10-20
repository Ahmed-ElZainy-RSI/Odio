# Plan: re-raise programming errors

**Spec:** [spec.md](spec.md)

## Approach

- Add `PROGRAMMING_ERRORS = (NameError, AttributeError, TypeError, KeyError, AssertionError)` to `service/ai_engine/domain/errors.py`.
- `retry.classify()` checks it first and returns "do not retry, internal".
- `run_page_task` maps it to the existing internal code in the taxonomy. If there is none, add `INTERNAL_ERROR` (non-retryable, HTTP 500 mapping).
- `src/pipeline`: the per-page `except` added by 008 becomes `except PROGRAMMING_ERRORS: raise` then `except Exception:`.

## Constitution check

V: INVARIANT-1 is preserved, because the page still gets one terminal report. X: the reason distinguishes bug from outage.

## Files

```
service/ai_engine/domain/errors.py
service/ai_engine/adapters/http/retry.py
service/ai_engine/application/run_page_task.py
service/tests/unit/test_retry.py            add cases
service/tests/e2e/test_fault_matrix.py      add injected TypeError row
src/pipeline/run_*.py                        (with 008)
```
