# Plan: final context review

**Spec:** [spec.md](spec.md)

```
service/ai_engine/adapters/ai/verifier/final_review.py   ← final_context_reviewer.py (validators kept)
service/ai_engine/config/settings.py                    FINAL_REVIEW=off|suggest, FINAL_REVIEW_MODEL
service/tests/unit/verifier/test_final_review.py
```

Output is mapped to token ids with `resolve.py` (022). Unresolvable suggestions are logged and dropped.
Overlaps with `tools/context_detector.py`, so share the review UI (`build_context_review.py`
format) so a human reviews both in one place.
