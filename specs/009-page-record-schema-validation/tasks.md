# Tasks: page record schema validation

- [ ] T001 Update the schema: add `skipped-empty`, `skipped-front-matter`, `failed`, `error`
- [ ] T002 [P] Tests: valid record passes, bad status / bad id / whitespace in `txt` fail in strict mode
- [ ] T003 `src/pipeline/records.py` (`read_record`, `write_record`, `audit`)
- [ ] T004 Replace writers in `src/pipeline/`
- [ ] T005 Replace writers in `tools/`
- [ ] T006 Grep test: no direct record writes outside `records.py`
- [ ] T007 Run the audit on `work/` and record the result here
