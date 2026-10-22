# Tasks: retire walidapro duplicates

Done as a full consolidation rather than a partial retirement: the repo owner's decision
(2026-10-22) was one codebase, keeping only walidapro's good parts, rewritten.

- [x] T001 Decision: the repo owner chose to retire the whole folder
- [x] T002 Local tag `walidapro-final` on the last commit that has the folder (not pushed)
- [x] T003 `walidapro/` removed entirely (72 tracked files). Kept, rewritten natively: the verification rules (022), the letter-preservation guard (006), the evidence record (018), the programming-error re-raise (007), the contract scenarios (030)
- [x] T004 Location map: `docs/pipeline-vs-walidapro-report.md` gains a "where each part went" section
- [x] T005 Architecture docs, ADR-009, comparison report updated
- [x] T006 `service/tests/unit/test_one_codebase.py`: no code imports walidapro, and the folder stays gone
