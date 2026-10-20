# Plan: page record schema validation

**Spec:** [spec.md](spec.md)

## Approach

- Update `specs/001-book-pipeline/contracts/page-record.schema.json` (statuses, `error`).
- Copy it to `src/pipeline/schema/page-record.schema.json` (runtime copy) with a sync test.
- `src/pipeline/records.py`: `read_record`, `write_record` (validates with `jsonschema` if installed, otherwise a minimal built-in check of required keys, status enum and the id pattern).
- Replace direct `path.write_text(json.dumps(rec...))` calls in `src/pipeline/*.py` and `tools/*.py`.
- `python3 -m pipeline.records audit work/` for the corpus audit.

## Files

```
specs/001-book-pipeline/contracts/page-record.schema.json   update
src/pipeline/schema/page-record.schema.json                 new (copy)
src/pipeline/records.py                                     new
src/pipeline/*.py, tools/*.py                               use write_record
src/tests/test_records.py                                   new
src/tests/conftest.py                                       PAGE_RECORD_VALIDATION=strict
```
