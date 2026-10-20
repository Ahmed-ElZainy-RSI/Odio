# Plan: cost metering

**Spec:** [spec.md](spec.md)

```
service/ai_engine/domain/usage.py                 UsageRecord, cost(price_table)
service/ai_engine/ports/usage.py                  UsageSink Protocol
service/ai_engine/adapters/persistence/usage_sql.py   usage table in job store DB
service/ai_engine/api/routers/ops.py              GET /ops/cost
config/prices.yaml                                shared by service + CLI
src/pipeline/usage.py                             JSONL sink: work/<book>/usage.jsonl
tools/cost_report.py                              CLI report
```

Hook point: the same place the provider budget charges (one wrapper per adapter call), so
nothing is metered twice or missed.
