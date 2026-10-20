# Plan: walidapro recovery

**Spec:** [spec.md](spec.md)

## Approach

1. Ask Waleed for a zip or a branch push of his working tree (`C:\Users\Waleed\Desktop\walidapro\walidapro`).
2. Copy over only the six missing files. Diff every other file against ours and record differences in `baseline.md` (his copy may be newer).
3. Create a venv inside `walidapro/`, install `requirements-dev.txt` plus `paddlepaddle` (CPU), and run `pytest -q`.
4. Untrack runtime data.

## Constitution check

| Principle | Note |
|---|---|
| VIII Test without paying | walidapro's suite is designed to be hermetic. Confirm no network calls happen (run it offline) |
| X Honest reporting | Baseline failures are recorded, not fixed silently |

## Files

```
walidapro/api/ai_contract/processing.py      restore
walidapro/ocr_verifier/tts/gemini_tts.py     restore
walidapro/api/jobs.py                        restore
walidapro/api/routers/review.py              restore
walidapro/api/routers/pipeline.py            restore
walidapro/tests/conftest.py                  restore
walidapro/.gitignore                         add api_output/, __pycache__/, .pytest_cache/, .qodo/
specs/005-walidapro-recovery/baseline.md     new
```

## Risk

Waleed is unavailable → fall back to FR-002 (reconstruct). That adds roughly 2–3 days and
lowers confidence in the baseline.
