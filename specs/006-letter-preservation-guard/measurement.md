# Measurement: real diacritizer vs the guard (T008)

**Date:** 2026-10-20 · **Script:** a scratchpad script (not committed); the method is below.

## Method

`work/` is not on disk, so the corpus came from the page data embedded in the 9 committed
readers (`git show HEAD:output_work/<book>/reader.html`, the `const P=[...]` array). For every
page: take each Arabic token's final text, strip it to letters only (`strip_marks`), run the
**real** `arabic-diacritizer` model (local CPU, no API calls) on the page, and check the output
with `check_letters_preserved`.

## Result

| | |
|---|---|
| Books | 9 |
| Pages | 95 |
| Words | 3,759 |
| Pages preserved | 94 |
| Pages rejected | 1 |
| Words where the **model** changed a letter | **0** |

The one rejection was **not** a model error. Token `p010w014` in `فلفل المغرور-lighton` is
stored as `'يَصْرُخُ؟! '`, with a trailing ASCII space. The VLM arbiter returned the space
in its verdict, and `tools/apply_corrections.py:89` wrote it into the token verbatim. Tokens
must not contain whitespace (`service/ai_engine/domain/tokens.py`, schema in 009), and
`tashkel.py`'s join/split cannot round-trip it, so the guard rejected the page correctly and
left it untouched. Root-cause fix tracked in [017](../017-vlm-third-answer-routing/spec.md)
(FR-005).

## What this means

- On this corpus the BiLSTM respects letters. The guard costs nothing in false rejections from
  the model itself.
- Its value is as a tripwire: it already caught one real upstream data defect, and it will
  catch any future diacritizer (visual per-word in 023, or a model swap) that does change
  letters.
