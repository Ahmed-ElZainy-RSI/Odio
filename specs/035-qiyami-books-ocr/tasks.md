# Tasks: «قيمي» books OCR into `result/`

- [x] T001 Tune the setup on scored pages (`tune_flash.py`, variants in `results_tune/`): v2 + high + zoom 3 + vote 5 chosen (FR-001)
- [x] T002 `amana_run.py`: one book → `result/<book>/` images, json, book.json, `_كامل.txt`; `--rebuild` (FR-002, FR-004, FR-005)
- [x] T003 `--book` option: folder named by title, not file name (FR-003)
- [x] T004 Run الأمانة (26), الأنانية (26), التعاون (25), no skipped page (SC-001)
- [x] T005 Viewers: `result/<book>/<book>.html` and `result/كتب_قيمي.html`; text checked against the published page (FR-006, SC-002)
- [x] T006 Scripts moved into `tools/qiyami/` (`qiyami_run.py`, `tune_flash.py`, `score_ocr.py`, `build_viewers.py`, `viewer_template.html`); raw runs copied to `work/qiyami/<book>/`. Checked: `--rebuild` of الأنانية and التعاون gives page JSON identical to before; `build_viewers.py` gives the same text on all 77 pages and every image exists
- [ ] T007 Read العند with the same setup (`work/العند` has 138 pages from the main pipeline; the `result/` deliverable does not exist yet)
- [ ] T008 Reference text for at least 2 pages of الأنانية and التعاون, so their tashkeel accuracy is measured and not assumed from الأمانة

Verification (2026-11-11): logs `run_anania.log`, `run_taawon.log` show 0 skipped pages;
`result/الأنانية/json` 26 files, `result/التعاون/json` 25 files; combined viewer text = page JSON text on all 77 pages.
