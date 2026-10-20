# Plan: correction accuracy harness

**Spec:** [spec.md](spec.md)

```
references/<book>/pNNN.txt              hand transcripts (UTF-8, one page per file)
references/README.md                    how they were made, by whom, conventions
tools/score_corrections.py              run(work_dir, ref_dir) -> Report
src/tests/test_score_corrections.py     synthetic cases for caught / false edit / missed
```

Metrics: WER/CER via edit distance over letter-normalized tokens/chars; DER computed only
where the reference has marks. Reuse `normalize_for_compare` from `tools/ensemble_review.py`.

Reference creation is manual (~1 h per book, per README). Ibn Battuta already exists, so locate
and move it under `references/`.
