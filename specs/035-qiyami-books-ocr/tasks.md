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

## 2026-11-15: الثقة, tashkeel review, reader UI + timing

- [x] T009 الثقة read (25/25 pages, `--book الثقة`); every word checked against the PDF's own text layer
  (`tools/qiyami/check_textlayer.py`), every difference confirmed on a 300-dpi crop: raw OCR 83.4% of
  words identical with the print (letters + tashkeel) → 129 hand fixes in `tools/qiyami/fixes/الثقة.json`,
  applied by `qiyami_run.py` on every run and `--rebuild`; each page JSON lists its `corrections`.
- [x] T010 Timing: `qiyami_run.py` times render + OCR per page with `src/pipeline/timing.py` into
  `work/qiyami/<book>/timings.jsonl`. الثقة and الاجتهاد (one uninterrupted run each) have per-page times
  estimated from the raw files' timestamps, step `ocr (estimated)`; the other books were run before timing.
- [x] T011 Reader UI before audio: `build_reader.build(..., preview=True)` shows unspoken pages with no
  audio and accepts a record `image` path; `tools/qiyami/build_readers.py` writes `result/<book>/reader/`
  (page records + `reader.html`) for every book. Hand-fixed words carry evidence (original word + why).
  TTS waits for OCR approval.
- [x] T012 The old HTML viewers are removed (`result/<book>/<book>.html`, `result/كتب_قيمي.html`,
  `build_viewers.py`, `viewer_template.html`): the reader (`result/<book>/reader/reader.html`) replaces them.
  FR-006 / SC-002 above describe the removed viewers; they are kept in git history.
- [x] T013 `result/<book>/reader/` is the pipeline's book folder up to OCR approval (no audio): page records
  with word boxes (line boxes from the PDF text layer → `lines_to_tokens`-style split → `ink_boxes.fit_boxes`),
  `book.json`, `timings.jsonl` / `usage.jsonl` (qiyami runs now record both), `check_pages/` + `ocr_check.html`
  (`tools/build_ocr_check.py`), `reader.html` (preview). Pages with no text layer (البرمائيات, covers) have no boxes.
- [ ] T014 Tashkeel review (T009's loop) for الأمانة 80.7%, الأنانية 82.8%, الاجتهاد 80.1%, التعاون 78.3%
  (words identical with the text layer, raw OCR); التسامح is at 97.5%. الأمانة p8 came back as one line with
  bare alefs (اَنَّكَ): re-OCR it. The older books' text still holds the running footer (| <story title>).
- [ ] T015 Boxes for pages without a text layer (البرمائيات 1+2): needs line boxes from the image (a paid
  Gemini box call, or an ink line detector).
- [x] T016 `result/<book>/` is now laid out exactly like a pipeline book folder (cf. `result/دبدوب الطماع`), minus
  `audio/`: `pages/`, `check_pages/`, `pNNN.json`, `book.json`, `ocr_check.html`, `reader.html`, `timings.jsonl`,
  `usage.jsonl`. The OCR stage's own output (`images/`, `json/`, qiyami `book.json`, `<book>_كامل.txt`) moved to
  `work/qiyami/<book>/` next to the raw runs; `qiyami_run.py` writes there and builds `result/<book>/` at the end.
  `ocr_check_<book>.json` appears when someone exports their marks from `ocr_check.html`.
