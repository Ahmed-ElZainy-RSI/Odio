# Plan: retire walidapro duplicates

**Spec:** [spec.md](spec.md)

1. Confirm with Waleed.
2. `git tag walidapro-final`.
3. Remove `walidapro/api/`, `run_api.py`, `run.py`, `run_full_pipeline.py`, `ocr_verifier/tts/`, `tests/`, `api_output/`, and the `ocr_verifier/` modules already ported.
4. Rewrite `walidapro/README.md` as a location map. Keep `docs/` and the audit reports for history.
5. Update `docs/backend-v1/01-current-architecture.md`, `19-adrs.md` (ADR-009), `docs/pipeline-vs-walidapro-report.md`, `specs/README.md`.
