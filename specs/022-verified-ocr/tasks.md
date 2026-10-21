# Tasks: verified OCR

Tests first (providers faked):
- [ ] T001 [P] `decide()` table tests
- [ ] T002 [P] Blind-read request contains the image only
- [ ] T003 [P] UNCLEAR gates (confidence, multi-word, equidistant)
- [ ] T004 [P] `resolve.py`: unique, repeated → REVIEW, substring blocked, box overlap tie-break
- [ ] T005 [P] Reviewer JSON repair; a failed review raises (not 0 suspects)
- [ ] T006 [P] LightOn early stop never reads the candidate

Implementation:
- [ ] T010 Port `arabic.py`, `decision.py`
- [ ] T011 Port `reviewer.py`
- [ ] T012 Port `localize.py`, `crops.py`
- [ ] T013 Port `lighton_reread.py`, `blind_reader.py`
- [ ] T014 `resolve.py`
- [ ] T015 `WalidaproPageVerifier` assembling the stages + evidence (018) + cache (021) + budget
- [ ] T016 Settings + `worker.py` wiring
- [ ] T017 Dockerfile `worker-verified` target
- [ ] T018 e2e on the recorded page
- [ ] T019 Persist verdicts per page (moved from 020 FR-005): table in the job store or a per-page artifact. Choose once real verdict volume is known; today `page.verified` is only logged
