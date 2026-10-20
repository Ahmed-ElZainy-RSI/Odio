# Plan: dependency lock files

**Spec:** [spec.md](spec.md)

## Approach

- Tool: `uv` (fast, lock files cover extras).
- `service/`: `uv lock`, then switch the Dockerfile and `.github/workflows/service-ci.yml` to `uv sync --frozen --extra ai`.
- Root: add a `pyproject.toml` (name `ocr-review-pipeline`, extras `pipeline`, `tools`, `dev`), then `uv lock`. Replace the `vx2lm` references with `uv run`.
- CI job `lock-check`: `uv lock --check`.

## Files

```
service/uv.lock, service/Dockerfile, .github/workflows/service-ci.yml
pyproject.toml (root, new), uv.lock (root, new)
README.md, docs/full-pipeline-guide.html, docs/tools-reference.html   remove vx2lm paths
```
