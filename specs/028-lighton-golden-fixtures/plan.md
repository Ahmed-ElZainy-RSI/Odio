# Plan: golden fixtures

**Spec:** [spec.md](spec.md)

```
service/tests/fixtures/{lighton,gemini}/<book>/pNNN.{response,expected}.json
service/tests/unit/test_ocr_golden.py      parametrized over fixtures
service/devtools/record_ocr_fixture.py     paid, manual capture
```

If raw responses for existing pages weren't persisted, capture new ones (budget: ~6 calls).
