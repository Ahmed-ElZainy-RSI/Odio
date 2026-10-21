# Feature Specification: Reproducible environments

**Feature:** `011-dependency-lockfile` · **Created:** 2026-10-20 · **Status:** Implemented 2026-10-21
**Priority:** P2 (roadmap `T-0.1`)

## Problem

- `service/pyproject.toml` pins floors only, so two builds can differ.
- The root `requirements.txt` has 15 bytes. The real `src/pipeline` deps (google-genai, hume, faster-whisper, arabic-diacritizer, PyMuPDF, python-dotenv) live in an undocumented conda env `vx2lm` at a user-specific path.
- `walidapro/requirements.txt` is unpinned, and PaddlePaddle is installed by hand.

## User Story 1 — Same deps on every machine and in CI (P2)

**Acceptance:** a fresh clone can install each component from a lock file, and CI installs
from the same lock. `docker build` is reproducible.

## User Story 2 — No machine-specific interpreter paths in docs (P3)

`README.md` and `tools/` docs don't reference `/Users/ahmedmostafa/miniconda3/envs/vx2lm`.

## Requirements

- **FR-001** `service/`: `uv lock` (or `pip-compile`) producing `service/uv.lock` / `requirements.lock`, used by the Dockerfile and CI.
- **FR-002** Root: `requirements-pipeline.txt` (or a `pyproject.toml` with extras `pipeline`, `tools`) listing every import in `src/pipeline` + `tools`, plus a lock.
- **FR-003** `walidapro/` deps folded into the service `verifier` extra when 022 lands.
- **FR-004** CI fails if the lock is stale.

## Success Criteria

- **SC-001** `pip install -r <lock>` + tests pass on a clean venv for each component.
