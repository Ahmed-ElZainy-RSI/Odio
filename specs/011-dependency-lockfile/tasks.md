# Tasks: dependency lock files

- [x] T001 Import inventory (AST walk over `src/pipeline`, `tools/`, `src/tests`) → `requirements-pipeline.in` (pymupdf, pypdf, pillow, google-genai, hume, faster-whisper, arabic-diacritizer, requests, python-dotenv, jsonschema, pytest); all confirmed on PyPI
- [x] T002 Pipeline lock: `requirements-pipeline.lock` (universal, Python 3.11). **Not** a root `pyproject.toml`: that could change which lint config applies to `src/` and `tools/`. Root `requirements.txt` now just `-r requirements-pipeline.lock`
- [x] T003 [P] Service locks: `service/requirements.lock` (runtime: `ai` + `metrics`, used by the Dockerfile; no test tools in the image) and `service/requirements-dev.lock` (+ `dev`, used by CI). Pinned to the versions of the env where every suite passes (`vx2lm`), not to "latest"
- [x] T004 [P] CI: `update_locks.sh --check` (fails on a stale lock), install **from the locks** (both, no pin conflicts across 62 shared packages), `pip check`; pip cache keyed on the locks. Dockerfile installs `requirements.lock`, then the package with `--no-deps`
- [x] T005 Machine-specific interpreter paths (`/Users/…/miniconda3/envs/vx2lm/bin/python3`, 14 places in README, TESTING, two docs pages, a tool and conftest) replaced by `.venv/bin/python3`, with the one-time setup `python3.11 -m venv .venv && .venv/bin/pip install -r requirements-pipeline.lock`
- [x] T006 **Clean-venv proof**: a fresh Python 3.11 venv per lock, nothing but the lock installed: service **761 passed, 0 skipped**; pipeline **202 passed**. `uv pip check`: all compatible. `--check` verified both ways (an up-to-date lock reproduces byte for byte; adding an absent package to the `.in` reports STALE and exits 1)

`service/devtools/update_locks.sh` regenerates all three locks. Each lock is constrained by itself, so pins move only when `pyproject.toml` / `requirements-pipeline.in` change. `CONSTRAINTS=<pip freeze>` re-pins from a known-good env.

Found by the clean-venv run (both real bugs, both fixed in `service/pyproject.toml` `dev` extra):
- **`python-multipart` was never declared.** The contract fake parses the Keycloak token form; without it **81 service tests fail**. It only worked because walidapro's requirements happened to install it into the shared env. CI installed from `pyproject.toml` floors, so it was passing by luck or not at all.
- **`jsonschema` was never declared** for the service tests (the evidence-contract test silently skipped).
- Also: `pyproject.toml` declares `structlog>=24.1`, but the env where all tests passed has 23.3.0. The lock resolves 26.1.0, which passes the full suite in the fresh venv.

Not verified locally: the Docker image build (the local Docker daemon, Colima, isn't running). CI builds the image on every run.

Verification (2026-10-21): fresh venvs as above; `vx2lm` `src/tests` 202 passed; service `ruff` clean; workflow YAML parses.
Branch: `feature/011-dependency-lockfile`, merged into `dev`.
