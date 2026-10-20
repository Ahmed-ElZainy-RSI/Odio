# Plan: contract replay

**Spec:** [spec.md](spec.md)

1. List scenarios in `walidapro/tests/test_ai_contract.py` (and `test_validation.py` for `/api/jobs` cases).
2. For each: rewrite against `service/tests/fakes/backend.py` (the contract fake) and the service `TestClient`.
3. Compare each assertion with the V1 PDF; write `differences.md`.

```
service/tests/behaviour/test_walidapro_scenarios.py
specs/030-contract-replay-walidapro-tests/differences.md
docs/backend-v1/22-open-questions.md
```
