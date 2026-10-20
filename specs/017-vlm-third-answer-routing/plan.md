# Plan: VLM third-answer routing

**Spec:** [spec.md](spec.md)

- `tools/vlm_arbiter.py`: after parsing verdicts, compute `agreement` using `normalize_for_compare` (already in `ensemble_review.py`).
- `tools/apply_corrections.py`: skip `third-answer` and append it to `<book>-lighton/context_review.json` with `source: "vlm-third-answer"`.
- `tools/build_context_review.py`: a section per source.
- `tools/apply_context_suggestions.py`: already applies human-accepted entries only, so no change apart from accepting the new source.

```
tools/vlm_arbiter.py, apply_corrections.py, build_context_review.py
src/tests/test_apply_corrections.py           add third-answer cases
src/tests/test_build_context_review.py        new section rendered
```
