# Implementation Plan: Verified Tier

**Feature:** `004-verified-tier-walidapro` · **Date:** 2026-10-20 · **Spec:** [spec.md](spec.md) · **Research:** [research.md](research.md)

## Summary

Lift `walidapro/ocr_verifier/` stages 2–4 into `service/ai_engine/adapters/ai/verifier/` as a
`PageVerifier` implementation that works on our token ids. Port the letters-unchanged guard
into both tiers. Retire walidapro's API, orchestrator and TTS.

## Technical context

| | |
|---|---|
| Language | Python ≥ 3.11 (walidapro targets 3.10+, compatible) |
| New deps | paddleocr + paddlepaddle (CPU), openai SDK (OpenAI-compatible Gemini endpoint), pypdfium2 |
| Providers | LightOn parse-v3, Gemini text (reviewer), Gemini vision (blind read, diacritics), gemini-3.1-pro (final review, opt-in) |
| Testing | fakes per provider; table tests for `decide`; property tests for preservation guard (hypothesis) |
| Constraints | provider budget mandatory; `ENABLE_VERIFIED_TIER=false` default |

## Constitution check

| Principle | Status | Note |
|---|---|---|
| I | ✅ | Verification before diacritization, in-process |
| II | ⚠️→✅ | Requires re-keying to token ids (R3) |
| III | ✅ | Blind read, UNCLEAR = 0, context capped |
| IV | ✅ | Preservation guard, duration gate |
| V | ✅ | Runs inside `run_page_task`, one terminal report |
| VI | ✅ | Skipped on text-layer pages |
| VII | ⚠️ | 40–200 calls/page. Justified as opt-in + budgeted (see Complexity) |
| VIII | ✅ | Hermetic fakes |
| IX | ✅ | Lives in `adapters/` only |

## Target structure

```
service/ai_engine/adapters/ai/verifier/
  __init__.py            WalidaproPageVerifier (implements ports.ai.PageVerifier)
  reviewer.py            ← walidapro/ocr_verifier/reviewer.py
  localize.py            ← bbox_detector.py (PaddleOCR, boxes only)
  crops.py               ← cropper.py
  lighton_reread.py      ← lighton_ocr.verify_crop_variants
  blind_reader.py        ← visual_reviewer.py
  decision.py            ← decision.py (unchanged logic)
  apply.py               token-id application (replaces _locate_unique_word_span)
  visual_diacritics.py   ← diacritization/word_diacritics.py (+ bounded concurrency)
  final_review.py        ← final_context_reviewer.py (opt-in)
  cache.py               fingerprinted cache + atomic writes
service/ai_engine/artifacts/preservation.py   letters-unchanged guard (both tiers)
src/pipeline/tashkel.py                        calls the same guard
walidapro/                                     → archived (README pointer) after port
```

## Complexity tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| 40–200 paid calls/page (VII) | Only way to verify letters against the image per word | Page-level VLM (`tools/vlm_arbiter.py`) is not blind and cannot localize |
| PaddleOCR dependency | Word boxes on scanned pages | Gemini line boxes subdivided by letter count are approximations |
