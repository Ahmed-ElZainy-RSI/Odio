# Feature Specification: Diacritization never changes letters

**Feature:** `006-letter-preservation-guard` · **Created:** 2026-10-20 · **Status:** Implemented in the CLI pipeline (2026-10-20). The service caller arrives with 016 · **Epic:** 004
**Priority:** P1. No API cost, protects both tiers, can start today

## Problem

`src/pipeline/tashkel.py` already refuses a page when the model returns a different **word
count** (`tashkel.py:50`). It does not check that each word's **letters** survived. The BiLSTM
can still swap a letter, drop a hamza or change punctuation inside a word, and the result is
fluent, wrong audio (constitution I and IV). walidapro already has the check:
`_check_preservation()` / `_find_first_difference()` in
`walidapro/ocr_verifier/diacritization/rebuild_text.py` (around lines 494 and 536).

## User Story 1 — A diacritizer that alters a letter is caught (P1)

**Given** input tokens and diacritizer output, **When** diacritics (and tatweel) are stripped
from both, **Then** they must be identical token by token. Otherwise the page keeps its
pre-diacritization text, the record gets `diac_error` with the first difference, and the
driver moves on to the next page.

**Acceptance:**
1. Output `الْفَضْلِ` for input `الفصل` → rejected, `diac_error` names token id + position.
2. Output adds or removes a punctuation mark → rejected.
3. Output that only adds harakat/shadda/sukun/tanween → accepted.
4. A page is either fully diacritized or untouched. It is never partly diacritized.

## Requirements

- **FR-001** One shared pure function `assert_letters_preserved(before: list[str], after: list[str]) -> PreservationResult` (stdlib only).
- **FR-002** *(resolved 2026-10-20)* Both sides are NFC-normalized, then these marks are stripped: U+0610–U+061A (Quranic signs), U+064B–U+0652 (tanween, harakat, shadda, sukun), U+0656–U+065F, U+0670 (dagger alef), U+06D6–U+06ED (Quranic small marks), U+0640 (tatweel). **U+0653–U+0655 (madda, hamza above/below) count as letters.** Under NFC they compose with the letter before them (ي + ◌ٔ → ئ), so adding one turns `مريي` into `مرئي`: a letter change. This deliberately differs from walidapro, which strips U+064B–U+065F wholesale.
- **FR-003** Called by `src/pipeline/tashkel.py` and by the service diacritizer adapter (016) and visual diacritics (023).
- **FR-004** Rejection is recorded on the page and counted in run summaries.

## Success Criteria

- **SC-001** Property test: random diacritics added to any Arabic string always pass; any single letter substitution, insertion or deletion always fails.
- **SC-002** Re-running `run_tashkel` on existing books reports how many pages would now be rejected (a measurement only, no rewrite).
