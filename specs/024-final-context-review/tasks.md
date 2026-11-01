# Tasks: final context review

- [x] T001 `apply` mode: **not built**, per the recommendation (text-only evidence can't authorise a letter change, constitution III). `FINAL_REVIEW=off|suggest`; `apply` is rejected at startup. If the tech lead later wants it, it is a new mode, not a flag flip
- [x] T002 [P] Tests: `tests/unit/test_final_review_rules.py` (the 5 checks, noise/reply cleaning, token mapping), `tests/unit/test_final_review.py` (0 tokens changed: SC-001; ≤ 2 completions: SC-002; unsafe not retried; outage skips the stage)
- [x] T003 Reviewer rewritten natively: prompts word for word from the prototype (`prompts.FINAL_REVIEW_*`, checked identical to tag `walidapro-final`); checks in `domain/final_review_rules.py`; adapter `adapters/ai/verifier/final_review.py`
- [x] T004 Mapped to token ids: one-for-one word changes become REVIEW verdicts (`detector="reviewer"`, `rule="final-review:suggest"`, `reads.llm`); splits, merges, insertions and deletions have no single token and are counted (`dropped`) and logged. Tokens the visual verifier already judged are skipped
- [x] T005 Export: `GET /ops/verdicts?jobId=…&format=review` returns the REVIEW verdicts (visual and final-review alike) in the CLI's `context_review.json` shape (`pages → [{id, ocr_word, proposed, suspicion, model, source}]`), `source` naming the detector and rule. The HTML page itself (`build_context_review.py`) also needs the CLI's `ensemble_review.json` beside it, so it is not driven from the service

Differences from the prototype, on purpose:
- output is suggestions, never rewritten text;
- at most 2 completions per page (prototype: up to 3 attempts, each with SDK retries);
- an outage or quota error skips the stage instead of failing the page (the stage is optional);
- words are counted after removing marks (the prototype counted letters on marked text, so each haraka split a word and skewed the 8% / 18% ratios on diacritized pages);
- speaker labels are checked per token (`word:`), since the service joins tokens on one line.

Model: `FINAL_REVIEW_MODEL=gemini-3.1-pro-preview` (the prototype's choice). **Not in `config/prices.toml`**: the cost report shows it as unpriced, never free. Add its rate before turning `suggest` on.

Verification (2026-10-24, vx2lm): service 912 passed including slow; `ruff` clean.
Branch: `feature/024-final-context-review`.
