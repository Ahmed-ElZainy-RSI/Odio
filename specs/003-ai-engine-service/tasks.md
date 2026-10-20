# Tasks: AI Engine Service

Task ids mirror `docs/backend-v1/21-implementation-roadmap.md`. Done list and evidence:
`service/docs/07-status-and-roadmap.md`.

## Done

- [x] T-1.1–T-1.6 Packaging, settings, domain + ports, error taxonomy, observability
- [x] T-2.1–T-2.6 App, JWKS auth, DTOs, idempotent accept, worker loop, debug GET
- [x] T-3.2–T-3.7 Page count, three OCR engines, tokenizer + Text artifact, Job B, timing artifact
- [x] T-4.1 (SQLite) – T-4.5 Durable store, page ledger, artifact ledger, outbox, reconciler
- [x] T-5.1–T-5.7 Token provider, retry policy, source fetch, metadata gate, upload, callbacks, provider budget
- [x] T-6.1–T-6.3, T-6.5, T-6.6 Contract fake, INVARIANT-1 suite, auth suites, artifact tests, CI
- [x] T-7.1–T-7.3 Container, compose, dockerignore

## Remaining. Each one is its own feature

- [ ] [016-service-diacritization](../016-service-diacritization/): the `Diacritizer` port has no adapter, so Job A text is never diacritized
- [ ] [020-page-verifier-port](../020-page-verifier-port/): the port the docs describe doesn't exist yet
- [ ] [027-postgres-job-store](../027-postgres-job-store/) (T-4.1)
- [ ] [028-lighton-golden-fixtures](../028-lighton-golden-fixtures/) (T-6.4)
- [ ] [011-dependency-lockfile](../011-dependency-lockfile/) (T-0.1)
- [ ] [029-source-fetch-redirect-policy](../029-source-fetch-redirect-policy/)
- [ ] [010-pymupdf-import-rename](../010-pymupdf-import-rename/)
- [ ] [031-production-config-hardening](../031-production-config-hardening/) (Q-01, allowed hosts, budgets)
- [ ] [007-reraise-programming-errors](../007-reraise-programming-errors/)
- [ ] [025-cost-metering](../025-cost-metering/)
- [ ] Verify locally: the service suite needs its venv (system Python lacks fastapi, measured 2026-10-20)
