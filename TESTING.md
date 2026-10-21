# Testing — real scenarios, not hypothetical ones

Every scenario below actually happened while building `tools/` against two
real books (`المومياوات ناشيونال جيوجرافيك`, `ثعلوب المكار`). This is the
scenario catalog: what to check, on what kind of input, and what the correct
behavior looks like, backed by a real run that already exercised it. Where a
scenario found a real bug, the bug and fix are named so the next person
doesn't have to rediscover it.

**Most of the scenarios below now have an automated regression test.** They
live in `src/tests/` (pytest) and run against fixture data, not live API
calls or real quota:

```bash
# one-time, pinned environment (spec 011): python3.11 -m venv .venv && .venv/bin/pip install -r requirements-pipeline.lock
.venv/bin/python3 -m pytest src/tests/ -v
```

Plain `python3 -m pytest src/tests/` also works and covers everything except
`test_ocr_resumability.py` (needs `fitz`/`google-genai`, so it skips cleanly
under an environment without them rather than erroring). Each scenario below
that has a test names its file; scenarios without one are still open —
tracked honestly in §6.

Companion docs: `README.md` (what's built), `docs/ocr-correction-architecture-built.html`
(why), `docs/tools-reference.html` (per-tool detail), `docs/full-pipeline-guide.html`
(the runbook).

---

## 1. Detection (`ensemble_review.py`)

### 1.1 Two engines fully agree on a page
**Setup:** a page where LightOn and Gemini produce the same text.
**Expected:** every token gets `action: "KEEP"`, `agree: true`. Nothing downstream touches these tokens.
**Observed:** page 13 of the pilot book — 0 disagreements, correctly never appeared in any later tool's output.

### 1.2 Ordinary word-level disagreement
**Setup:** a page where most words match but a handful differ.
**Expected:** matching tokens `KEEP`, the rest `FLAG`, aligned via `difflib.SequenceMatcher` after diacritic/alif/tatweel normalization (so a diacritic-only difference doesn't falsely flag).
**Observed:** pilot book pages 15/17/18/19 — 5–13% flagged, in the range the original design doc expected.

### 1.3 One engine drops a word entirely
**Setup:** LightOn produces nothing where Gemini has a real word (or vice versa).
**Expected:** an `insert`/`delete` opcode produces an entry with `lighton_txt: null` and id prefixed `gemini-only:` (or `gemini_txt: null` for the reverse) — reported, not silently dropped.
**Observed:** pilot book, 114 `gemini-only` entries total, concentrated on page 16 (see §3.2).

### 1.4 One engine fails almost the entire page
**Setup:** LightOn produces ~1 real word where Gemini has ~75.
**Expected:** the page's flag rate is dominated by `gemini-only` entries (not scattered word-level `FLAG`s) — the shape of the disagreement, not just its rate, is the signal that this needs `replace_page.py`, not `apply_corrections.py`.
**Observed:** pilot book page 16 — 74/75 tokens `gemini-only`. Root cause found by actually looking at the page image: white text reversed out of a dark background with a ghosted watermark behind it.

### 1.5 Punctuation/markdown noise causing false disagreement
**Setup:** one engine attaches trailing punctuation (`مِصْرَ،`) or markdown emphasis (`**text**`) differently than the other.
**Expected:** normalization strips it before comparison, so these don't count as real disagreements.
**Bug found and fixed:** the first version of `normalize_for_compare` only handled the diacritics/alif-form table from the original design doc, not punctuation — 22 of 234 initial flags on the pilot book were pure punctuation noise. Fixed by stripping everything but letters/digits before comparing.

### 1.6 Re-running against a book already partially compared
**Setup:** `ensemble_review.json` already covers pages 1–17; the underlying book now has pages 1–33 (e.g. after finishing a stalled Gemini OCR run).
**Expected:** only the new pages (18–33) get computed; pages 1–17's existing entries — including any `vlm_arbiter.py` verdicts — are left untouched.
**Bug found and fixed:** the original `run()` always rebuilt the whole report from scratch. Re-running it after extending OCR coverage would have silently discarded 234 already-arbitrated (real, paid API call) verdicts. Fixed to skip pages already present in the output file.

**Automated:** all of §1.1–1.3, 1.5, 1.6 → `src/tests/test_ensemble_review.py` (16 tests).

---

## 2. Arbitration (`vlm_arbiter.py`)

### 2.1 Normal flagged page
**Setup:** a page with a handful of `FLAG` entries.
**Expected:** one Gemini vision call for the whole page (not one per word), reading the real image and writing what's actually printed — not just picking between the two OCR candidates.
**Observed:** pilot book, 234 flagged tokens across 17 pages, resolved in 17 calls, 0 API failures.

### 2.2 Both original candidates are wrong
**Setup:** LightOn and Gemini both misread the same word differently, and neither is what's printed.
**Expected:** the verdict is a genuine third answer, not a forced pick between the two given candidates — the prompt explicitly asks for what's actually there, not a multiple choice.
**Observed:** 17/234 pilot-book verdicts (7%) were novel third answers. Some were real fixes (Gemini's own TOC dot-leader noise stripped correctly). One was a numeric/ISBN string that got yet a *third* different digit grouping — flagged in docs as lower-confidence than the flat `VERIFIED` label implies.

### 2.3 A word that doesn't exist on the page at all
**Setup:** one engine hallucinated a word that isn't actually printed anywhere.
**Expected:** the verdict is an empty string, distinct from "pick one of the candidates."
**Observed:** one case on the pilot book (`نَعْدُّ` → `""`). Handled downstream as a real deletion, not a text substitution (see §3.3).

### 2.4 Mid-run crash loses nothing already done
**Setup:** a 429 (quota) interrupts arbitration partway through a multi-page run.
**Expected:** every page already arbitrated before the crash is on disk; re-running resumes at the next un-arbitrated page.
**Bug found and fixed:** the original `run()` only wrote the output file once, after the entire loop finished. A crash mid-run — which happened for real, twice — silently discarded every page completed before it, including real API calls already paid for. Fixed to write after every page.

**Automated:** none of §2 yet — `vlm_arbiter.py`'s core logic is a real Gemini vision call, and it hasn't been factored so the request-building/response-parsing can be tested without mocking that call. Tracked in §6.

---

## 3. Applying corrections

### 3.1 Ordinary word substitution
**Setup:** a `VERIFIED` entry whose word differs from the current token.
**Expected:** `txt` is overwritten, the old value moves to `ocr_txt` (reversible), `"ensemble-vlm"` is appended to `fixes[]`, page `status` resets to `"extracted"`.
**Observed:** 81–99 corrections applied per book run, confirmed reversible via `ocr_txt`.

### 3.2 Gemini-only words are never inserted
**Setup:** a page has real word-level corrections *and* several `gemini-only` gaps.
**Expected:** the word-level corrections apply; the `gemini-only` entries are reported (grouped by page, with counts) and left alone — no id/position/box is guessed for them.
**Observed:** pilot book — 99 applied, 114 reported unapplied across 5 pages, dominated by page 16 (see §3.4 for how that page was actually fixed).

### 3.3 A verdict says the word doesn't exist
**Setup:** `verified_txt == ""`.
**Expected:** the token is deleted from the page's token list outright — not written as an empty string. Safe because nothing downstream indexes by id (the reader walks by array position, the diacritizer re-matches by position against whatever's left).
**Observed:** one deletion on the pilot book, page 15's token count correctly dropped by one on re-diacritization.

### 3.4 A page is a wholesale failure, not scattered errors
**Setup:** ≥50% of a page's tokens are `gemini-only`.
**Expected:** use `replace_page.py`, not `apply_corrections.py` — the whole page's token list (including `box`/`box_level`) is swapped for Gemini's, since both engines already emit the same token schema.
**Observed:** pilot book page 16 — 75 tokens replaced wholesale; the book's `المومياوات` (ثعلوب المكار had no page cross this threshold — a useful negative case, see §3.5).

### 3.5 No page crosses the replacement threshold
**Setup:** a book where LightOn's worst page is still well under 50% `gemini-only`.
**Expected:** `replace_page.py` reports "no pages at or above the threshold" and does nothing — `apply_corrections.py` alone handles the whole book.
**Observed:** `ثعلوب المكار` — max `gemini-only` ratio across all 12 pages was 8%, `replace_page.py` correctly found nothing to do.

### 3.6 Re-running the apply step against an already-corrected book
**Setup:** every `VERIFIED` correction from a book has already been applied and the book has been fully re-diacritized.
**Expected:** running `apply_corrections.py` again finds **zero** new corrections — the book is stable.
**Bug found and fixed:** `vlm_arbiter.py` often returns undiacritized text (`حكايات`) even when the current token already has correct diacritics from an earlier `run_tashkel` pass (`حكاياتِ`) — same word, same letters. The original exact-string check treated that as a mismatch, stripped the diacritics back off, and forced a pointless re-diacritize + re-TTS cycle (burning real, scarce TTS quota) on pages that needed zero changes. Fixed by comparing letters only (`normalize_for_compare`).

### 3.7 A page swapped wholesale breaks id-based pairing
**Setup:** `replace_page.py` has run on a page; a later tool (`build_review.py`) tries to pair "what LightOn originally said" against "the current token" by id.
**Expected:** the tool detects the page was wholesale-replaced (via `ocr_engine_replaced_from`, which `replace_page.py` sets) and falls back to sourcing the original text from `ensemble_review.json`'s untouched historical record instead of trying id-based pairing that can't work anymore (the ids are now Gemini's, not LightOn's).
**Observed:** pilot book page 16 — `build_review.py` correctly produced a near-silent "original OCR" audio track for the page (just the one real word LightOn produced) without crashing on the id mismatch.

**Automated:** §3.1–3.6 → `src/tests/test_apply_corrections.py` (8 tests) and `src/tests/test_replace_page.py` (9 tests). §3.7 (`build_review.py`'s fallback) is not — it also needs a live Gemini TTS call to exercise end to end. Tracked in §6.

---

## 4. Correlated errors (`context_detector.py` / `apply_context_suggestions.py`)

### 4.1 Both engines agree, and they're both wrong
**Setup:** a `KEEP` word that's actually a misreading both engines share.
**Expected:** flagged with a suspicion score and a proposed correction — the one class of error nothing else in the pipeline can see, since detection elsewhere is purely "do the two engines disagree."
**Observed:** confirmed by hand against the real page image — `ثعلوب المكار` page 2, `الْغِنَاءُ` ("singing") where the real text says `الْغَنَّاءِ` ("lush", describing a forest). **The model's own proposed fix for this case was also wrong** (it just repeated a neighboring word) — a concrete demonstration of why nothing from this layer auto-applies.

### 4.2 A flag is really a diacritizer error, not an OCR error
**Setup:** a flagged word differs from the proposal only in case-ending diacritics.
**Expected:** distinguishable from a genuine different-word flag, since these are far more common (in the observed runs, roughly 7:1 case-ending vs. different-word) and more debatable — often the automated diacritizer's own ~6.6% DER, not the OCR engines'.
**Observed:** `build_context_review.py` tags every entry `diff` or `case` by comparing normalized forms, specifically so the higher-value "different word" entries aren't buried among case-ending noise.

### 4.3 Nothing is ever auto-applied
**Setup:** `context_detector.py` finds suspicious words.
**Expected:** every entry defaults to `"reject"` in `context_decisions.json`; `apply_context_suggestions.py` only touches an id whose decision was explicitly changed to `"accept"` or a literal replacement string.
**Observed:** both test books — 109 and 29 entries respectively, all still `"reject"` pending human review at time of writing.

### 4.4 Something else changed a token between detection and applying its suggestion
**Setup:** `context_decisions.json` was edited to accept a suggestion, but the underlying token's text changed since (another correction pass, a manual edit).
**Expected:** the apply step detects the mismatch (letters-only comparison, same reasoning as §3.6) and skips that entry, reporting it rather than blindly overwriting; a diacritic-only drift (not an actual letter change) must NOT be treated as stale.
**Observed:** not yet triggered on a real run, but now covered by an automated test exercising both directions — a genuine letter change (skipped) and a diacritic-only drift (still applied).

### 4.5 A reasoning model's real "thinking" time looks like a hang
**Setup:** `context_detector.py` calls a reasoning model (`deepseek-v4-pro-0813` via OpenRouter); a page with 50+ words to check can take several minutes of genuine computation.
**Expected:** don't kill and retry within the first few minutes — check actual process state (CPU time, open network connections) before concluding it's stuck.
**Observed:** a page that appeared frozen for 12+ minutes with 0% CPU turned out to be genuinely still processing — it completed normally, and killing/retrying similar cases wasted real progress. A separate case *was* a genuine multi-hour stall (near-zero CPU, no forward progress across a much longer window) and needed a kill + retry. Both are real; the distinguishing signal is whether elapsed time keeps growing with no state change at all versus periodic real progress.

**Automated:** §4.2, 4.3, 4.4 → `src/tests/test_build_context_review.py` (7 tests) and `src/tests/test_apply_context_suggestions.py` (7 tests). §4.1 (the detector itself) and §4.5 (latency behavior) are not — both need a live OpenRouter call. Tracked in §6.

---

## 5. Resumability and quota

### 5.1 A quota wall stops a multi-page run partway
**Setup:** any TTS or Gemini-vision call hits a 429 mid-book.
**Expected:** the driver prints which page failed, saves everything completed so far, and re-running the exact same command later resumes from that page — at zero cost for anything already done.
**Observed:** hit repeatedly and for real: `gemini-3.1-flash-tts` (10 requests/day) during both books' TTS; `gemini-3.6-flash` (20 requests/day) during OCR and arbitration on the same day. Every resume worked correctly once the underlying per-page status checks were fixed (see §5.2).

### 5.2 Re-running the base pipeline on an already-corrected book
**Setup:** `main.py --ocr lighton` is run again against a book that already went through the full correction chain (some pages reset to `"extracted"` by `apply_corrections.py`, expecting `run_tashkel`/`run_speak_gemini` to pick them up).
**Expected:** `run_ocr_lighton.py`/`run_ocr_gemini.py` recognize the page is already past extraction (status `"diacritised"` or `"spoken"`, not just `"extracted"`) and skip it — corrections survive.
**Bug found and fixed:** both drivers only checked `status == "extracted"`. A page reset to `"extracted"` by the correction pipeline would be silently re-OCR'd from scratch on the next full-pipeline run, destroying every applied correction. This is the same bug class `run_extract.py` already had a documented real incident about (see README's Open Items) — just never fixed on the OCR-engine drivers until this session. Fixed identically on both.
**How to re-verify:** run `tools/run_full_pipeline.py` twice in a row on the same book. The second run should report 0 new corrections, 0 pages re-diacritized, and every OCR stage should say "already done, skipping" for every page. Automated: `src/tests/test_ocr_resumability.py` — parametrized over all three "already done" statuses for both engines, plus the exact cross-tool sequence (a page `apply_corrections.py` reset to `"extracted"` must still be skipped, not re-OCR'd), using a real minimal PDF and monkeypatched network calls (no live API needed, no quota spent).

### 5.3 The one-command orchestrator on a fresh book vs. a resumed one
**Setup:** `tools/run_full_pipeline.py` run on a PDF with no `work/` output yet, versus run again on the same PDF after a partial completion.
**Expected:** fresh run does the full Gemini-then-LightOn-then-rename dance; a resumed run detects `work/<book>-lighton` already exists and skips straight to the comparison/correction stages.
**Observed:** both paths exercised on `ثعلوب المكار` — first run did the full OCR dance, second and third runs correctly skipped it and went straight to (now-idempotent) comparison.

---

## 6. Known gaps — genuinely untested, not just undocumented

**No automated coverage, and the reason is the same for all of them: the
function under test IS the network call.** None have been refactored to
separate "build the request" / "parse the response" from "make the HTTP
call," which is what would make them mockable without either hitting a real
API or asserting nothing meaningful:

- `vlm_arbiter.py`'s `arbitrate_page` — the Gemini vision request/response handling itself (§2.1–2.3).
- `context_detector.py`'s `detect_page` — the OpenRouter request/response handling itself (§4.1).
- `build_review.py` end to end — needs a live Gemini TTS call to produce the dual audio tracks (§3.7).
- Quota-exhaustion handling (§5.1) — a real 429 mid-loop, and that the driver saves progress before stopping. The *shape* of this (save-before-stop) is exercised indirectly by `vlm_arbiter.py`'s per-page-write fix, but no test actually injects a failure partway through a loop.
- `run_full_pipeline.py` itself — it's a thin orchestrator over already-tested pieces, but the orchestration logic (fresh vs. resumed run, the folder move/rename dance) has no test of its own.

**Smaller, genuinely open gaps:**

- No test exercises a book that fails `has_text_layer()`'s threshold ambiguously (near the 50-word cutoff).
- No test covers what happens if `OPENROUTER_API_KEY` or `GEMINI_API_KEY` is missing/invalid — currently just an unhandled `KeyError`/API error, not a friendly message.
- `run_ocr_lighton.py`'s per-page failure handling (a bad LightOn response mid-loop) isn't exercised, only its resumability check.
