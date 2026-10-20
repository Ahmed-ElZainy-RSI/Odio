# Tasks: service diacritization

- [ ] T001 [P] `SyntheticDiacritizer` + unit tests (mismatch, preservation failure, kept page)
- [ ] T002 [P] e2e test: undiacritized fixture → diacritized Text artifact; Job A → Job B ids round-trip
- [ ] T003 `LocalDiacritizer` adapter (no global model)
- [ ] T004 Call from page task after tokenization + 006 guard
- [ ] T005 Setting + metric
- [ ] T006 Dockerfile build arg for weights; update `service/docs/03-configuration.md`
