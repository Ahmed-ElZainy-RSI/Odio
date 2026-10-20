# Plan: PageVerifier port

**Spec:** [spec.md](spec.md)

```
service/ai_engine/ports/ai.py                     + PageVerifier Protocol, VerificationResult, Verdict
service/ai_engine/domain/evidence.py              (from 018) Verdict.evidence type
service/ai_engine/adapters/ai/verifier_passthrough.py   PassThroughVerifier
service/ai_engine/application/run_page_task.py    call verifier when job.quality_tier == VERIFIED and page.source == ocr
service/ai_engine/worker.py                       inject verifier (passthrough unless 022 configured)
service/ai_engine/adapters/persistence/sql.py     page_verdicts table (versioned migration)
service/tests/unit/test_page_verifier_contract.py
service/tests/e2e/test_verified_tier_routing.py
```

Constitution IX: the port lives in `ports/`, adapters in `adapters/ai/`, and the application only
knows the Protocol.
