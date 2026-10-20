# Plan: letter preservation guard

**Spec:** [spec.md](spec.md)

## Approach

Write the function once, as a stdlib-only module, in the service's domain layer, because
`domain/` already mirrors pipeline concepts and is import-safe. `src/pipeline` gets a thin
copy under `src/pipeline/preservation.py`, and a test asserts the two stay byte-identical.
That avoids a reverse dependency `src → service` (see the layering rules, constitution IX).

## Constitution check

| Principle | Note |
|---|---|
| I, IV | This feature *is* principle IV for diacritization |
| II | Comparison is per token id, so the error names the exact token |
| VIII | Pure function, property-tested with hypothesis |

## Files

```
service/ai_engine/domain/preservation.py          new (stdlib only)
src/pipeline/preservation.py                      new (identical copy)
src/pipeline/tashkel.py                           call guard after the count check (line ~50)
src/pipeline/run_tashkel.py                       count + print rejected pages
service/tests/unit/test_preservation.py           property tests
src/tests/test_tashkel_preservation.py            integration with a fake model
src/tests/test_preservation_copy_in_sync.py       the two copies are identical
```

## Reference

Port the comparison from `walidapro/ocr_verifier/diacritization/rebuild_text.py`
(`_check_preservation`, `_find_first_difference`), keeping its Arabic reasoning comments.
