# Plan: verified OCR

**Spec:** [spec.md](spec.md)

## Structure

```
service/ai_engine/adapters/ai/verifier/
  __init__.py          WalidaproPageVerifier
  reviewer.py          ← walidapro/ocr_verifier/reviewer.py         (ContextReviewer)
  localize.py          ← bbox_detector.py                           (PaddleOCR, boxes only)
  crops.py             ← cropper.py                                 (word/context crops, 5 variants)
  lighton_reread.py    ← lighton_ocr.py verify_crop_variants + _select_best_visual_reading
  blind_reader.py      ← visual_reviewer.py                         (GeminiVisualReviewer)
  decision.py          ← decision.py                                (unchanged)
  resolve.py           new: suspect → token id (replaces _locate_unique_word_span)
  arabic.py            ← utils.py normalization
service/tests/unit/verifier/   one test module per file, providers faked
service/tests/e2e/test_verified_ocr.py   recorded page work/فلفل المغرور-lighton/p003.json
```

## Porting rules

- Keep logic and thresholds. Change only I/O (inject HTTP clients, use 021 cache, return evidence).
- Keep the Arabic reasoning comments. Reformat to `ruff` style.
- Every provider call goes through the shared retry policy (`adapters/http/retry.py`). Remove walidapro's own retry loops to avoid stacked retries (risk R-04).

## Image

PaddleOCR + paddlepaddle in a separate Dockerfile target `worker-verified`.

## Constitution check

III: this feature is the strongest implementation of III. VII: opt-in, budgeted, measured (025).
II: guaranteed by 020's contract check.
