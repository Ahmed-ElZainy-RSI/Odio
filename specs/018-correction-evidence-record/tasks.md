# Tasks: correction evidence record

- [x] T001 `contracts/evidence.schema.json`. It is the page-record schema's `$defs.evidence`, plus `$defs.pageEvidence` for whole-page swaps; a test asserts the two definitions are identical
- [x] T002 `service/ai_engine/domain/evidence.py`: stdlib frozen dataclass with validation (`applied` only on `ACCEPT`, known detectors/deciders/reads), `to_dict`/`from_dict`. Verbatim copy at `src/pipeline/evidence.py` for the CLI tools, with a sync test (same pattern as 006)
- [x] T003 [P] Tools write evidence:
  - `apply_corrections.py`: `detector=ensemble`, `decided_by=rule`, `rule=match-lighton|match-gemini` (the 017 classification), reads `{lighton, gemini, vlm}`, model when known
  - `apply_context_suggestions.py`: `decided_by=human`, `rule=human-accept|human-custom`; context entries → `detector=context`, reads `{llm}`, `scores.suspicion`, model; VLM third answers (017) → `detector=ensemble`, reads `{lighton, gemini, vlm}`
  - `replace_page.py`: record-level `page_evidence` (`rule="gemini-only >= 50%"` or `"manual --page"`, ratio, replaced/replacement engine, token count)
  - provenance going forward: `vlm_arbiter.py` stores `vlm_model` on each verdict; `context_detector.py` stores `model` on each suspect
- [x] T004 [P] Tests: every written fix validates (the 009 strict-mode writer checks each entry against the schema); content asserted per tool; rerun adds no duplicate evidence; routed third answers write none — `src/tests/test_evidence_tools.py` (10), `service/tests/unit/test_evidence.py` (9)
- [x] T005 Display: the latest evidence is shown in the **reader's word inspector** (`build_reader.py`: "corrected from الفضل · ensemble detector, decided by rule (match-gemini) · 2026-10-21"). That's where a person looks at a word, so it beat the plan's `build_review.py`

Design notes:
- `fixes` stays a list of strings (the reader and the 009 schema depend on it); `evidence` is a new per-token list, one entry per applied correction, oldest first. `ocr_txt` still holds the original OCR text.
- Mapping from walidapro's `SuspiciousWord` is in [plan.md](plan.md). The verified tier (020/022) writes the same shape with `detector=verifier`, `decided_by=verifier`, `rule=decide()`.
- Reviews arbitrated before this change have no `vlm_model`. Their evidence simply omits `models`.

Verification (2026-10-21, vx2lm): `src/tests` 147 passed (was 137), system Python 130 + 9 skipped; service 672 passed including slow (was 663); `ruff` clean on new code.
Branch: `feature/018-correction-evidence-record`, merged into `dev`.
