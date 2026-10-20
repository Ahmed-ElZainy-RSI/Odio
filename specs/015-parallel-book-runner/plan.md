# Plan: parallel book runner

**Spec:** [spec.md](spec.md)

- `src/pipeline/batch.py`: discover PDFs, order them (text layer first), `ProcessPoolExecutor(workers)`, each worker calls `main.run_pipeline()`.
- `src/pipeline/ratelimit.py`: SQLite-backed sliding-window limiter (`acquire(provider)` blocks or raises `QuotaPaused`). Called inside `tts_gemini`, `tts_hume`, `ocr_gemini`, `ocr_lighton` request functions.
- Reuse the window logic idea from `service/ai_engine/domain/budget.py` (fixed epoch-aligned windows).

```
src/pipeline/batch.py, ratelimit.py      new
src/pipeline/tts_*.py, ocr_*.py          acquire() before each request
src/tests/test_ratelimit.py, test_batch.py
```

Depends on 008 (failures don't kill a worker) and 013 (stable work dirs).
