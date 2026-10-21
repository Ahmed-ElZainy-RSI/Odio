# Tasks: correction accuracy harness

- [ ] T001 Locate the existing Ibn Battuta transcript. **Not in the repo:** git history has only its scores (`models/ibn_battuta/OCR model comparison.html`: "538 words, 355 distinct"), not the text. Whoever made it needs to add it under `references/` (listed as wanted in `references/README.md`)
- [ ] T002 Transcribe `الشمس` (16 pages) and one undiacritised book. **Manual, about 1 h per book**; format and rules in `references/README.md`
- [x] T003 [P] Synthetic tests — `src/tests/test_score_corrections.py` (11): perfect page; caught (`الفضل`→`الفصل`), false edit and missed, each by token id; diacritics and punctuation don't count as letter errors; alef forms are *not* folded; insertions and deletions count in WER; DER 0.2 on a one-mark-wrong word; no DER on an undiacritised reference; book-level run (pages without a record skipped, non-page files ignored); JSON output
- [x] T004 `tools/score_corrections.py`: WER/CER before and after (true edit distance on letters), caught / false edits / missed via stable token ids (before = `ocr_txt` where a fix was applied), DER on tashkeel-printed references, word-weighted book totals, text or `--json`; `references/README.md` (layout, transcription rules, wanted list)
- [ ] T005 Baseline run (raw OCR vs current `tools/` chain on the reference books) → `baseline.md`. Needs T001/T002 and `work/` on disk

Design choices:
- Scoring normalisation is **stricter** than `normalize_for_compare` (used for engine agreement): it drops diacritics, tatweel and punctuation, but keeps alef forms and ة/ه, because for accuracy those are real OCR errors.
- A token deleted by a correction is gone from the record, so it can only appear as a deletion in "after". The records don't keep deleted tokens, and that's noted in the tool.

Verification (2026-10-21, vx2lm): `src/tests` 179 passed (was 168); system Python 157 + 14 skipped; `ruff` clean on new code.
Branch: `feature/019-correction-accuracy-harness`, merged into `dev`.
