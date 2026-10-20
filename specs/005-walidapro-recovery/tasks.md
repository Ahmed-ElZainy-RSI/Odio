# Tasks: walidapro recovery

- [ ] T001 Request the complete working tree from Waleed
- [ ] T002 Restore the six empty modules
- [ ] T003 [P] Diff his tree against ours, list other differences in `baseline.md`
- [ ] T004 `walidapro/.venv`, `pip install -r requirements-dev.txt paddlepaddle`
- [ ] T005 Run `pytest -q` offline, record counts and failures in `baseline.md`
- [ ] T006 Start `run_api.py`, check `/health` and `/docs`
- [ ] T007 [P] Update `walidapro/.gitignore`; `git rm -r --cached walidapro/api_output walidapro/__pycache__ walidapro/.pytest_cache walidapro/.qodo`
- [ ] T008 Fallback only: reconstruct any file that can't be recovered (FR-002)
