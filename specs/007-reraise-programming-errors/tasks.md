# Tasks: re-raise programming errors

- [x] T001 [P] Tests: OCR, TTS and alignment adapters let `NameError`/`AttributeError`/`TypeError` through; real provider errors are still wrapped; a bug is not counted in `ai_engine_inference_failures_total` — `service/tests/unit/test_programming_errors.py` (14 tests)
- [x] T002 [P] e2e: a `TypeError` in TTS → exactly one failed callback, `INTERNAL_ERROR`, reason says "internal error" and not "provider" — `tests/e2e/test_job_b_end_to_end.py::test_a_programming_error_is_reported_as_internal_not_as_the_provider`
- [x] T003 `PROGRAMMING_ERRORS` in `service/ai_engine/domain/errors.py`; uses the existing `INTERNAL_ERROR` code, no new code needed
- [x] T004 Retry layer: no change needed. `send_with_retry` only catches `httpx.HTTPError`, so a programming error was never retried there. The masking happened in the adapters instead: `tts.py`, `ocr_providers.py` and `aligner_whisper.py` each wrapped *every* exception as `AiInferenceError`. All three now re-raise programming errors first
- [x] T005 `run_page_task` (Job A) and `run_audio_job` (Job B) map them to `INTERNAL_ERROR` with reason "internal error (TypeError) in the engine's own code" and log event `*.programming_error`
- [x] T006 CLI: done with 008 (`src/pipeline/failures.py`)

Found while implementing:
- [x] T007 `GeminiTts._request` indexed straight into the response (`candidates[0].content.parts[0]…`), so an **empty provider response** raised `TypeError`/`IndexError`. Once programming errors pass through, that would have been misreported as a bug. The shape is now checked explicitly (`_audio_bytes`), and an empty response is `AiInferenceError("… no audio part")` (4 tests)

Decided not to do:
- `INTERNAL_ERROR` stays in the retryable set. Nothing in the service reads `.retryable` (page retries come from leases and the reconciler), so flipping it would change the taxonomy table without changing behaviour. Revisit if a consumer of the flag is ever added.
- `PyMuPdfRenderer.page_count` keeps its broad `except`: an untrusted PDF crashing a native library is input (`SOURCE_INVALID`), not our bug.

Verification (2026-10-20, `vx2lm`): service 636 passed + 9 slow passed (was 620 + 9); `src/tests` 102 passed; `ruff check` and `ruff format --check` clean.
