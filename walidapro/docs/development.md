# Development

- [Known gaps in the current working tree](#known-gaps-in-the-current-working-tree)
- [Running the tests](#running-the-tests)
- [Test suite layout](#test-suite-layout)
- [Code conventions](#code-conventions)
- [Adding a pipeline stage](#adding-a-pipeline-stage)
- [Audit reports](#audit-reports)

---

## Known gaps in the current working tree

**Twelve `.py` files are 0 bytes.** Six are legitimate package markers; the other six are
not, and they break the application in ways worth knowing before you debug anything else.

Verified with `find . -name '*.py' -size 0` and `git log` on 2026-09-08.

### Legitimate — empty package markers

```
api/__init__.py
api/ai_contract/__init__.py
api/routers/__init__.py
ocr_verifier/__init__.py
ocr_verifier/tts/__init__.py
tests/__init__.py
```

(`ocr_verifier/diacritization/__init__.py` is **not** in this list — it is 44 bytes and
carries a real module comment.)

### Blocking — modules with real work missing

| File | Git history | Impact |
|---|---|---|
| `ocr_verifier/tts/gemini_tts.py` | **19,649 bytes in `HEAD`**, emptied in the working tree (`git status`: ` M`) | `ocr_verifier/tts/pipeline.py:9` does `from .gemini_tts import GeminiTTS` ⇒ **`ImportError`**. All of Stage 5 and `POST /api/v1/tts/generate` are dead. |
| `api/routers/review.py` | never committed (0 commits) | `api/main.py:6` imports it, then `app.include_router(review.router)` ⇒ **`AttributeError` at startup**. |
| `api/routers/pipeline.py` | never committed | Same — `app.include_router(pipeline.router)` ⇒ **`AttributeError` at startup**. |
| `api/ai_contract/processing.py` | never committed | `api/routers/ai_jobs.py` calls `processing.submit(...)`, `processing.process_pages_extraction_job`, `processing.process_audio_timing_job` ⇒ **`AttributeError` on every accepted job**. |
| `api/jobs.py` | never committed | Not imported anywhere. Probably a leftover; safe to delete or fill. |
| `tests/conftest.py` | never committed | Every test uses the `client`, `sample_pdf`, `ai_contract_env` and `requests_mock` fixtures ⇒ **the entire suite errors on collection**. |

### Net effect

- `python run_api.py` — **does not start.** `api/main.py` fails on the first empty router.
- `pytest` — **collects nothing.** All fixtures are missing.
- `run.py` (OCR verification only) — **works**, it never touches the API layer or TTS.
- `run_full_pipeline.py` — **does not start.** It imports `TTSPipeline` at module level
  (`run_full_pipeline.py:30`), which imports `GeminiTTS`, so the failure is at import time —
  not at Stage 5. Stages 1–4 are intact; only this entry point's import chain is broken.
- `python -m ocr_verifier.pdf_type_detector` — works.

### Recovering `gemini_tts.py`

This one has a committed version and can be restored directly:

```bash
git show HEAD:walidapro/ocr_verifier/tts/gemini_tts.py > ocr_verifier/tts/gemini_tts.py
```

Check first whether it was emptied deliberately — the working-tree version is `M`, not
deleted, so someone truncated it rather than removing it.

### Reconstructing the rest

The four that matter have to be written from scratch, but their contracts are already
pinned down elsewhere in the repo, which makes the shape unambiguous:

| Module | Contract is defined by |
|---|---|
| `api/routers/review.py` | `FinalReviewRequest` / `FinalReviewResponse` in `api/schemas.py`; `tests/test_validation.py::test_final_review_*`; `FinalContextReviewer.review()` |
| `api/routers/pipeline.py` | `PipelineJobCreateResponse`, `PipelineJobStatusResponse`, `PipelineJobListItem`, `PipelineJobTextResponse` in `api/schemas.py`; the `pipeline_jobs` table in `api/db.py`; `tests/test_validation.py::test_pipeline_*` |
| `api/ai_contract/processing.py` | Must expose `submit(fn, *args)`, `process_pages_extraction_job(...)`, `process_audio_timing_job(...)`, and module-level `OCRVerificationPipeline` / `TTSPipeline` names (the tests monkeypatch those attributes). Behaviour is fully specified by `tests/test_ai_contract.py` |
| `tests/conftest.py` | Fixtures `client` (FastAPI `TestClient`), `sample_pdf` (a one-page image-only PDF with no text layer — asserted by `test_inspect_real_pdf_reports_ocr_required`), `ai_contract_env` (exposing `.backend_base` and `.token_url`), plus `requests_mock` |

See [api-reference.md](api-reference.md) for the reconstructed endpoint shapes.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

`pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

**No test calls a paid API.** Real OCR/Gemini/TTS success paths need real keys and are out
of scope for this suite — it verifies request validation, contract plumbing and safety
guards. Useful invocations:

```bash
pytest -v                          # verbose
pytest tests/test_pdf.py           # one file
pytest -k "idempotent"             # one test by name
pytest -x --lf                     # stop at first failure, rerun last-failed
```

> ⚠️ As noted above, the suite cannot currently run — `tests/conftest.py` is empty.

---

## Test suite layout

| File | Lines | What it covers | External APIs |
|---|---|---|---|
| `tests/test_health.py` | 7 | `GET /health` returns `{"status": "ok"}` | none |
| `tests/test_pdf.py` | 69 | PDF inspection and rendering — fully local PyMuPDF/pypdfium2 logic. Extension check, empty file, real image-only PDF ⇒ `OCR_REQUIRED`, PNG magic bytes, out-of-range page ⇒ 404, bad DPI ⇒ 400 | none |
| `tests/test_validation.py` | 94 | That FastAPI/Pydantic reject bad input **before** any external call is made: non-PDF uploads, empty text, missing fields, bad narrator, unknown ids, unparseable page selectors | none |
| `tests/test_ai_contract.py` | 377 | The whole `POST /api/jobs` contract — auth, token handling, idempotency, download-URL refresh, 3-step upload, status callbacks | mocked with `requests_mock` |

The design intent is stated in the test files themselves: endpoints that call paid APIs
can't have their success path exercised without real keys, so the tests prove that
validation happens *first* and that no external call is made on a bad request.

---

## Code conventions

Read a few files before contributing — the style is distinctive and consistent.

**Formatting.** Heavily vertical: one argument per line, blank lines between logical
groups, section banners as comment blocks:

```python
    # ============================================================
    # SECTION NAME
    # ============================================================
```

There is no formatter config in the repo (no `ruff`/`black` settings), so match the
surrounding file by hand.

**Typing.** `from __future__ import annotations` at the top of every module. Type hints on
all public signatures. `Literal` for closed sets (`Status`), frozen `@dataclass` for
settings, plain `@dataclass` for records.

**Comments and docstrings are bilingual.** Arabic for domain explanation ("why this rule
exists in Arabic OCR terms"), English for mechanics. Both are load-bearing — the Arabic
comments often carry the actual reasoning. Keep the pattern when editing.

**Error handling.** The most important convention in the codebase:

```python
# Programming errors must never be converted into an empty OCR reading.
if isinstance(exc, (NameError, AttributeError, TypeError)):
    raise
```

A bug must not masquerade as "the image was unreadable". Broad `except` blocks that swallow
failures into a neutral result are only acceptable where a genuine external failure is
expected — and even there, programming errors are re-raised first.

**Atomic writes.** Use `utils.atomic_write_text()` for any output file — it writes to
`.tmp` and `replace()`s, so a crash can't leave a half-written artifact that the cache
later trusts.

**Cache fingerprints.** Any new cache must include everything that could change the result.
The OCR cache key includes PDF SHA-256, DPI, OCR model and `max_side` precisely because
omitting one of those once caused stale results to survive a config change.

---

## Adding a pipeline stage

The existing stages share a shape worth copying:

1. **A module under `ocr_verifier/`** with one class, constructed with its config, exposing
   one primary method.
2. **A preservation or validation check** that mechanically verifies the stage did not
   exceed its mandate — `_check_preservation()` in diacritization,
   `_validate_result()` in the final reviewer, `_duration_is_sane()` in TTS. Write this
   *before* trusting the stage's output.
3. **A fingerprinted cache**, so reruns are free and config changes invalidate correctly.
4. **A router under `api/routers/`** plus request/response models in `api/schemas.py`,
   registered in `api/main.py`.
5. **Validation tests** in `tests/test_validation.py` proving bad input is rejected before
   any external call.
6. **A step in `run_full_pipeline.py`'s `process_page()`**, writing into its own
   subdirectory of `page_NNN/`, with a `page_status.json` flag updated after it completes.

The non-negotiable part is step 2. Every stage in this system is allowed to refuse; none is
allowed to guess.

---

## Audit reports

Two audits ship with the repo, both dated 2026-08-20.

**`FUNCTIONAL_AUDIT_REPORT.txt`** — 29 Python files, 0 AST syntax errors, `compileall` PASS,
executable guard tests PASS. Verifies nine specific fixes, including:

- `lighton_ocr.py` imports `re`
- `NameError` / `AttributeError` / `TypeError` are not swallowed by the visual-variant fallback
- corrections use a verified character span, not `str.replace(old, new, 1)`
- a repeated word is corrected only when its sentence is unique on the page *and* the token
  is unique within that sentence
- Arabic substring matches are blocked — `حسن` does not match inside `المحسن`
- diacritics-only candidates are deferred to visual diacritics rather than treated as OCR corrections
- the OCR cache fingerprint includes PDF SHA-256, DPI, OCR model and `max_side`

**`AUDIT_REPORT_AR.txt`** — the same review in Arabic, organised per module, with the
reasoning behind each guard.

Both close with the same caveat, which is worth repeating:

> These tests validate program behavior and safety guards, **not OCR model accuracy**. No
> OCR, Gemini, LightOn or TTS API was called during the audit. No OCR/AI system can
> guarantee every word is correct without running it on the page and comparing against the
> image or ground truth. This version is designed to prefer REVIEW / KEEP ORIGINAL over
> guessing.

Note that the audits predate the current working-tree state — they were run when
`gemini_tts.py` still had content.
