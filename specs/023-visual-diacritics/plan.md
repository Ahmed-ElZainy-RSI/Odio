# Plan: visual diacritics

**Spec:** [spec.md](spec.md)

```
service/ai_engine/adapters/ai/verifier/visual_diacritics.py   ← walidapro diacritization/word_diacritics.py
service/ai_engine/adapters/ai/verifier/rebuild.py             ← rebuild_text.py (token-based, no layout tokenizer needed)
service/ai_engine/application/run_page_task.py                route by tier + path; ≥50% REVIEW gate before TTS
service/tests/unit/verifier/test_visual_diacritics.py
```

Concurrency: `asyncio.Semaphore(VISUAL_DIACRITICS_CONCURRENCY)` around each word call;
results gathered into a dict keyed by token id. Rate limits are handled by the shared provider
budget, so the semaphore only bounds load.

`visual_diacritics.py` (walidapro's whole-page variant) is **not** ported: one approach only.
