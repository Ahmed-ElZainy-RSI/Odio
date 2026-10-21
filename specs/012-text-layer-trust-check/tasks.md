# Tasks: text-layer trust check

- [x] T001 [P] Tests — `src/tests/test_text_layer_trust.py` (13): folding (diacritics, alef forms, punctuation) scores the same words 1.0; another page's words score < 0.2; order matters; one ghost page distrusts the book; per-page scores cached by content + engine (a re-run or a threshold change costs no OCR, another engine re-checks); no sample page; `main.py` wiring (ghost → OCR path, `--trust-text-layer` skips the check, an unavailable check falls back loudly, a bug is not swallowed); and the **real ghost page** (شكرا يامى p2 text layer vs what LightOn actually read on it, from the record in git history) scoring 0.596 < threshold
- [x] T002 `src/pipeline/text_layer_trust.py`: `similarity` (order-aware, after folding), `sample_pages` (first rich page, middle, last; up to k), `assess` (worst sampled page decides, cache under `work/.trust/<book_id>-<engine>.json`), Gemini and LightOn samplers (the same engine as `--ocr`; usage recorded under stage `trust-check`, spec 025)
- [x] T003 `main.py`: the check runs before the text-layer path; `--trust-text-layer` skips it; if the check can't run (no key, quota) it falls back to the text layer with a loud `UNCHECKED` warning (today's behaviour) rather than blocking every text-layer book on an OCR outage
- [x] T004 Calibration, offline (no paid calls), on the only book with both a text layer and OCR output (شكرا يامى, 15 comparable pages, records recovered from git history). **Only pages 2–3 are ghost text** (0.596, 0.481); pages 4–16 match (0.714–1.0). Threshold set to **0.65**, inside that gap (0.6 would have sat on the bad page). Sampling picks pages 2 and 7, so this book is caught
- [ ] T005 Service `OCR_ENGINE=auto` uses the same check. Not done: the service's `auto` resolution would need the paid sampler wired into `build_ocr_engine`. Follow-up
- [ ] T006 Per-page instead of per-book (spec US2). **The calibration says this is the better design**: on شكرا يامى only 2 of 16 pages are ghost, yet per-book routing OCRs all 16. Doing it per page needs a check on *every* page (one OCR call each), which is the cost trade-off to decide

Known limits, from the calibration:
- One negative book, and **no positive book**: there's no book with a trustworthy text layer *and* an OCR run of it. The threshold's lower side (good pages ≥ 0.714) comes from the matching pages of the same book. A wider calibration needs 2 OCR calls on a few text-layer books
- A ghost page the sample doesn't land on is missed. Here the ghost pages are at the front and the sampler starts at the first rich page; that's not guaranteed for other books

Verification (2026-10-21, vx2lm): `src/tests` 192 passed (was 179); system Python 157 + 15 skipped; `ruff` clean on touched files.
Branch: `feature/012-text-layer-trust-check`, merged into `dev`.
