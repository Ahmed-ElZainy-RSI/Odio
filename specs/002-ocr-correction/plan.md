# Implementation Plan: OCR Correction

**Feature:** `002-ocr-correction` · **Date:** 2026-10-20 · **Spec:** [spec.md](spec.md)

## Summary

Detection by **engine disagreement** (LightOn vs Gemini) + **text-only correlated-error
detection**, resolution by **page-level VLM arbitration** and **human review**. All tools are
importable `run(...)` functions over the `001` page records; `tools/` imports `pipeline`, never
the reverse.

## Technical context

| | |
|---|---|
| Language | Python 3.10+, stdlib + requests/dotenv (vx2lm env for Gemini stages) |
| Providers | Gemini (`vlm_arbiter.py`), OpenRouter/DeepSeek (`context_detector.py`), LightOn + Gemini OCR via `001` |
| Storage | JSON files next to the book: `ensemble_review.json`, `context_review.json`, `context_decisions.json` |
| Testing | `src/tests/test_ensemble_review.py`, `test_apply_corrections.py`, `test_apply_context_suggestions.py`, `test_build_context_review.py`, `test_replace_page.py`, `test_ocr_resumability.py` |

## Constitution check

| Principle | Status | Note |
|---|---|---|
| I Correct before diacritize | ✅ | Fix resets downstream status |
| II Anchoring | ✅ | Fixes target token ids; `ocr_txt` kept |
| III Uncertain ⇒ keep | ⚠️ | VLM sees the page and the two reads → not blind (walidapro's crop protocol is stricter); human gate compensates for the context layer |
| IV Stage proves lane | ⚠️ | Letters-only idempotency check; no per-fix evidence record like walidapro's `SuspiciousWord` |
| V Isolation / resume | ✅ | Resumable per page |
| VII Cost | ✅ | 1 extra OCR + 1 VLM call per flagged page |
| VIII Tests | ✅ | |

## Project structure

```
tools/
  ensemble_review.py           detect (engine diff)
  vlm_arbiter.py               resolve (one vision call per flagged page)
  apply_corrections.py         apply word-level, reversible
  replace_page.py              apply page-level (≥50% gemini-only)
  context_detector.py          detect correlated errors (text-only, OpenRouter)
  build_context_review.py      HTML review
  apply_context_suggestions.py apply human-accepted only
  build_review.py              deep audit, original vs corrected, dual audio
  export_session.py            session export helper
  run_full_pipeline.py         orchestrator, stops before manual step
```
