# Tasks: parallel book runner

- [x] T001 [P] Limiter tests — `src/tests/test_ratelimit_batch.py`: limit parsing; a window is enforced then resets (injected clock); the most specific key (`provider:model`) wins and unconfigured providers are unlimited; an exhausted window is classified as quota by 008's `classify_failure`; **4 real processes racing for a limit of 10 are granted exactly 10**; the env-driven `acquire` is a no-op until `PIPELINE_RATE_LIMITS` is set; an exhausted window stops Gemini OCR **before any request is sent, and is not retried**
- [x] T002 `src/pipeline/ratelimit.py`: fixed epoch-aligned windows (a daily limit resets at 00:00 UTC), counters in one SQLite file under `BEGIN IMMEDIATE` (the service budget's technique), old windows pruned; `QuotaPaused` says "rate limit" so the drivers stop that provider's work and the next run resumes
- [x] T003 `acquire()` before every paid request: Gemini OCR and the VLM arbiter (before **each retry attempt**, outside the retry's `try`, so an exhausted window isn't retried), Gemini TTS, LightOn, Hume, OpenRouter
- [x] T004 `src/pipeline/batch.py`: process pool across books (one process per book: page records have a single writer), text-layer books first, one book's exception never stops the batch, per-book summary, exit code 1 if any book failed, Ctrl-C finishes running books and cancels the rest. `workers=1` runs in-process. Batch tests: ordering, failure isolation, pool == serial
- [ ] T005 Speed check with a fake TTS on 10 books (SC-001, ≥ 3× with 4 workers). Not measured: the useful number depends on real provider latency and quota, and with free-tier limits the limiter, not the pool, sets the pace

Configuration (opt-in; nothing set = nothing limited, exactly as before):

```bash
PIPELINE_RATE_LIMITS="gemini:gemini-3.1-flash-tts-preview=10/86400,gemini:gemini-3.6-flash=20/86400" \
  python3 src/pipeline/batch.py "assets/PDF Samples" --workers 4
```

Verification (2026-10-21, vx2lm): `src/tests` 202 passed (was 192); system Python 163 + 19 skipped; `ruff` clean on new files.
Branch: `feature/015-parallel-book-runner`, merged into `dev`.
