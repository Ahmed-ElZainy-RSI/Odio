# Tasks: dependency lock files

- [ ] T001 Inventory imports in `src/pipeline`, `tools` (script: AST walk) → dependency list
- [ ] T002 Root `pyproject.toml` with extras + `uv lock`
- [ ] T003 [P] `service/uv.lock`; Dockerfile uses `uv sync --frozen`
- [ ] T004 [P] CI: install from lock, add `uv lock --check`
- [ ] T005 Replace `vx2lm` paths in README and docs
- [ ] T006 Clean-venv install + tests for each component
