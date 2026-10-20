# Plan: text-layer trust check

**Spec:** [spec.md](spec.md)

## Approach

`src/pipeline/text_layer_trust.py`:

```python
def assess(pdf_path, ocr="gemini", sample=2) -> TrustResult  # trusted: bool, similarity, pages, reason
```

- Pick sample pages, OCR them with the existing `ocr_gemini` / `ocr_lighton` single-page function, extract text-layer words with `extract.py`, and compare.
- Cache `work/.trust/<sha256>.json`.
- `main.py`: `has_text_layer()` and `assess()` together decide the path. Add the `--trust-text-layer` flag.
- The service's `OCR_ENGINE=auto` (`adapters/ai/ocr_providers.py:build_ocr_engine`) gets the same check behind a setting, so both entry points agree.

## Constitution check

VI: still prefers the free path; spends at most 2 calls to justify it. VII: cost is stated and bounded.

## Files

```
src/pipeline/text_layer_trust.py     new
src/pipeline/main.py                 use it, new flag
service/ai_engine/adapters/ai/ocr_providers.py   optional check in auto resolution
src/tests/test_text_layer_trust.py   fake OCR, identical vs unrelated text
```
