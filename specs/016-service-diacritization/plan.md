# Plan: service diacritization

**Spec:** [spec.md](spec.md)

## Approach

- `adapters/ai/diacritizer_local.py`: `LocalDiacritizer` implements `ports.ai.Diacritizer`. It loads the `arabic-diacritizer` model in `__init__` (not as a module global) and calls the tashkel logic.
- `SyntheticDiacritizer` for tests (adds a fatha after each letter, which passes the preservation guard).
- `application/run_extraction_job.py` / `run_page_task.py`: after tokenization, if `diac_ratio < threshold`, call the diacritizer, then `domain.preservation` (006).
- `worker.py` composition root wires it in.

## Constitution check

I: only safe once correction exists. On the fast tier there is none in-service, so document it as an accepted risk (README `الفضل` example). II: positional re-bind, abandon on mismatch. VII: free.

## Files

```
service/ai_engine/adapters/ai/diacritizer_local.py   new
service/ai_engine/application/run_page_task.py       call it
service/ai_engine/worker.py                          wire it
service/ai_engine/config/settings.py                 DIACRITIZE_BELOW_RATIO
service/Dockerfile                                   weights build arg
service/tests/unit/test_diacritizer_local.py, tests/e2e/test_job_a_end_to_end.py
```

Depends on 006.
