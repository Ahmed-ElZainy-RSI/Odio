# Tasks: contract replay

- [x] T001 Scenario inventory: all 13 tests in `walidapro/tests/test_ai_contract.py`, mapped in [differences.md](differences.md). Done without Waleed's missing `conftest.py`: only the scenarios mattered, not his fixtures
- [x] T002 `service/tests/behaviour/test_walidapro_scenarios.py` (9): walidapro's **literal request bodies** replayed against our API (6 differences pinned, 3 agreements as smoke tests). The 10 agreeing scenarios that already had full coverage are mapped to existing tests rather than duplicated
- [x] T003 Each difference checked against the V1 PDF:
  - D1 `jobId` must be a GUID and D2 `folderId`/`pathPrefix` required: **service follows the spec**
  - D5 https + allowlisted source: spec silent, service stricter for SSRF
  - D3/D4 `422` vs `400`: spec silent → **Q-18**
  - D6 same GUID, different body: spec says "return success", service returns `409` → **Q-17**, reopened in the open-questions register (it was wrongly listed as "answered ourselves")
- [x] T004 No code change in the service: every difference is either spec-compliant or needs the Backend's answer. Today's behaviour is pinned by tests so a future change is deliberate
- [ ] T005 Share `differences.md` with Waleed and the Backend team. **Outward-facing; left for you**

Verification (2026-10-22, vx2lm): service 785 passed including slow (was 776); `ruff` clean; every test name in differences.md verified to exist.
Branch: `feature/030-contract-replay-walidapro-tests`, merged into `dev`.
