# Tasks: text-layer trust check

- [ ] T001 [P] Tests with fake OCR: identical → trusted; unrelated Arabic → not trusted; near-empty page rules
- [ ] T002 `text_layer_trust.assess()` + SHA-256 cache
- [ ] T003 `main.py` wiring + `--trust-text-layer`
- [ ] T004 Calibrate the threshold on the 36 sample books (records which were checked + similarity) → `calibration.md`
- [ ] T005 Service `auto` resolution uses the same check (setting `OCR_TEXT_LAYER_TRUST_CHECK=true`)
- [ ] T006 Resolve the per-page vs per-book question in spec.md
