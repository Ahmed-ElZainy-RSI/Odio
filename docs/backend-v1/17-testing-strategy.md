# Testing Strategy

## 1. The baseline

Measured, not assumed:

| | |
|---|---|
| Suite | 6 test files + `conftest.py`, **763 LOC, 56 tests**, in `src/tests/` |
| Config | `pytest.ini`, 2 lines: `testpaths = src/tests`, `python_files = test_*.py`. No `addopts`, no coverage, no markers, no `asyncio_mode`. |
| Runtime | **56 passed** under Python 3.11.15 (1.16 s and 2.30 s observed on two runs); 47 passed + 1 module-level skip in 0.08 s under Python 3.14 (`test_ocr_resumability.py` does `pytest.importorskip("fitz")`) |
| Network calls | **Zero, by design** — asserted structurally: monkeypatched functions *raise* if reached (`src/tests/test_ocr_resumability.py:52-54`, `:86-88`, `:132-134`) |
| Coverage | 5 of 10 `tools/` modules (`ensemble_review`, `apply_corrections`, `replace_page`, `build_context_review`, `apply_context_suggestions`) plus the resumability skip-branch of two `src/pipeline` drivers → **7 of 56 production modules** |
| **`walidapro/`** | **29 modules, 16,155 LOC (~83% of production Python), ZERO tests.** Not even collectible — `test_voices.py` has no test functions, so `pytest walidapro` collects nothing. |
| CI | **None.** No `.github/`, no `Makefile`, no pre-commit — and git history shows none ever existed. |

`TESTING.md` documents the gaps accurately and honestly: §6 (lines 184-202) lists exactly what is
untested — `vlm_arbiter.arbitrate_page`, `context_detector.detect_page`, `build_review` end to end,
429/quota mid-loop handling, `run_full_pipeline` orchestration, missing-API-key handling, and
`run_ocr_lighton` per-page failure. Its per-file test counts (16/8/9/7/7) match the collected counts
exactly.

**The 56 tests are good and stay verbatim.** They are hermetic, fast (1-2 s), and they encode real bugs with
real provenance rather than hypotheticals. They are also evidence of something useful: the five `tools/`
modules they cover are the most service-ready code in the repository *precisely because* they were made
testable.

## 2. What V1 testing must add

The critical insight: **none of the existing tests exercise anything the V1 spec requires.** There is
zero coverage of HTTP handling, auth, token refresh, retry classification, upload cycles, page-level
callback semantics, or partial-job success. That is not a criticism of the suite — those things do not
exist yet — but it does mean the V1 test suite is built from scratch alongside the code.

## 3. Layers

### 3.1 Unit tests — `tests/unit/`

Pure, no I/O, milliseconds. This is where the domain lives, and `ai_engine/domain/` imports only the
standard library specifically so these tests need nothing.

| Area | Named cases |
|---|---|
| Domain invariants | `test_job_completed_requires_all_pages_terminal`, `test_job_completed_allows_failed_pages` (REQ-META-02's own semantics), `test_page_has_exactly_one_terminal_state` |
| State machines | `test_valid_job_transitions`, `test_invalid_page_transition_rejected`, `test_terminal_states_are_absorbing` |
| Validators | `test_jobid_must_be_uuid`, `test_unknown_jobtype_rejected`, `test_page_number_required_for_job_b`, `test_page_number_forbidden_for_job_a`, `test_source_url_must_be_https`, `test_source_url_host_must_be_allowlisted`, `test_path_prefix_rejects_traversal` |
| Payload hashing | `test_same_payload_different_source_url_is_not_a_conflict` (a presigned URL legitimately changes between retries), `test_different_page_number_is_a_conflict` |
| Error model | `test_every_error_code_has_http_mapping`, `test_retryable_classification_matches_table`, `test_reason_string_is_bounded_to_512`, `test_reason_never_contains_url_or_token` |
| Token cache | `test_token_reused_above_30s_remaining`, `test_token_refreshed_below_30s_remaining` (REQ-AUTH-02), `test_token_never_persisted` |
| Retry policy | `test_backoff_has_full_jitter`, `test_retry_after_header_honoured`, `test_401_retried_once_then_terminal`, `test_403_and_404_never_retried` |
| Artifact builders | `test_text_artifact_is_utf8_without_bom`, `test_text_artifact_single_trailing_newline`, `test_empty_page_yields_newline_only`, `test_timing_json_matches_schema`, `test_timing_monotonic_and_non_overlapping`, `test_timing_within_duration_bounds`, `test_missing_t_omits_t_src` |
| Deterministic tokenizer | `test_same_text_same_page_yields_identical_ids`, `test_ids_are_contiguous_from_w000` |
| AI wrappers (with fake ports) | `test_ocr_adapter_maps_provider_429_to_rate_limited`, `test_adapter_never_raises_systemexit`, `test_diacritizer_never_changes_letters` |

### 3.2 Integration tests — `tests/integration/`

Real Postgres via `testcontainers`; all external HTTP faked.

| Area | Named cases |
|---|---|
| Idempotent accept | `test_duplicate_jobid_inserts_once`, `test_concurrent_identical_posts_create_one_job` (the ON CONFLICT race from [C-08](24-architecture-challenge.md)) |
| Queue claim | `test_two_workers_claim_different_jobs` (SKIP LOCKED), `test_expired_lease_is_reclaimable`, `test_attempt_counter_increments_on_reclaim` |
| Page ledger | `test_fanout_creates_exactly_pages_count_rows`, `test_page_rows_created_before_any_page_work` |
| Artifact ledger | `test_confirmed_artifact_not_reuploaded_on_retry`, `test_artifact_row_written_only_after_confirm_upload` |
| Outbox | `test_callback_row_written_in_same_transaction_as_state_change`, `test_undelivered_row_retried_with_backoff`, `test_delivered_row_never_resent`, `test_unique_constraint_blocks_double_completed` |
| Reconciler | `test_stale_nonterminal_page_force_failed`, `test_reconciler_is_idempotent`, `test_job_without_metadata_escalates_to_job_failed`, `test_orphan_scratch_dir_removed` |
| Retention | `test_terminal_jobs_purged_after_retention_days` |

### 3.3 API tests — `tests/api/`

FastAPI `TestClient`, fake Postgres or a real container, fake JWKS.

| Named case | Asserts |
|---|---|
| `test_valid_job_a_returns_202` | REQ-JOB-01 |
| `test_valid_job_b_returns_202` | REQ-JOB-03 |
| `test_duplicate_jobid_returns_202_and_duplicate_true` | REQ-IDEM-01 |
| `test_duplicate_jobid_different_payload_returns_409` | our PROPOSAL |
| `test_missing_token_returns_401` / `test_expired_token_returns_401` / `test_bad_signature_returns_401` | REQ-AUTH-06 |
| `test_valid_token_wrong_client_returns_403` | the `azp` allowlist |
| `test_hs256_token_rejected` | **algorithm-confusion regression test** |
| `test_alg_none_rejected` | ditto |
| `test_unknown_jobtype_returns_422` | not silently accepted |
| `test_non_json_content_type_returns_415` | |
| `test_postgres_down_returns_503_not_202` | **we must never claim to have accepted a job we could not persist** |
| `test_no_ai_module_importable_from_api_process` | the import-rule contract from [16-repository-structure.md](16-repository-structure.md) §2 |
| `test_health_always_200` / `test_ready_503_when_db_down` | probe semantics |
| `test_error_envelope_shape_and_request_id` | [14-error-model.md](14-error-model.md) §2 |

### 3.4 AI tests — `tests/ai/`

Model input/output validation and quality regression. **Stubbed providers by default.**

| Area | Named cases |
|---|---|
| Port contracts | `test_every_ocr_adapter_returns_contiguous_ids`, `test_ocr_empty_page_returns_empty_sequence_not_error` (today `walidapro/.../word_diacritics.py:1580` raises `RuntimeError("No Arabic word boxes were detected.")` on an illustration-only page — that must become an empty result, since the Backend still waits for that page) |
| **Safety invariants** | `test_diacritizer_preserves_consonantal_skeleton`, `test_rebuild_text_preservation_assertions_fire_on_tampering`, `test_unclear_visual_read_cannot_accept_letter_change`, `test_visual_reviewer_prompt_contains_no_candidate` (the blind-crop protocol at `walidapro/.../visual_reviewer.py:133-150`) |
| Alignment quality | `test_bind_is_monotonic_on_golden_pages`, `test_anchor_ratio_on_golden_corpus_not_below_baseline` — **the corpus baseline is 0.78 measured across 95 real pages**, so this is a true regression test with a real reference value |
| Degradation | `test_zero_anchors_still_produces_valid_timing_json`, `test_tts_duration_sanity_gate_rejects_degenerate_audio` (the real ~224 s-for-50-words failure recorded at `walidapro/ocr_verifier/tts/pipeline.py:493-495`) |
| Schema tolerance | `test_reader_tolerates_page_record_missing_ocr_engine` (text-layer records lack it), `test_reader_tolerates_word_vs_raw_key_drift` (`src/pipeline/extract.py` writes `word`; OCR paths write `raw`) |

### 3.5 End-to-end tests — `tests/e2e/`

Whole workflows against the contract fakes. No real provider calls.

| Named case | Asserts |
|---|---|
| `test_job_a_happy_path_three_pages` | metadata → 3 × (image, text, completed), correct order |
| `test_job_b_happy_path_single_page` | audio, json, completed |
| `test_expired_source_url_recovers` | REQ-SRC-02: presigned 403 → `get-download-url` → success |
| `test_malformed_pdf_fails_whole_job` | REQ-CB-05, and **no page callbacks are sent** |
| `test_page_3_fails_page_4_times_out_all_pages_still_report` | **the INVARIANT-1 test** |
| `test_worker_sigkill_mid_page_recovers` | one terminal report per page, no duplicate confirmed upload |
| `test_full_book_from_real_corpus` | a real PDF from `assets/PDF Samples`, provider responses replayed from golden fixtures |

### 3.6 Performance tests — `tests/performance/`

Not a benchmark suite; a small set of guards against the specific things we know are risky.

| Named case | Guard |
|---|---|
| `test_page_task_respects_deadline` | A stubbed 20-minute provider call is cut off at `PAGE_TASK_TIMEOUT_SECONDS` and reports `PROCESSING_TIMEOUT` |
| `test_scratch_high_water_mark_bounded` | Concurrent page tasks do not exceed the computed ~100 MB envelope ([11-data-and-storage.md](11-data-and-storage.md) §4) |
| `test_streaming_renderer_does_not_materialize_all_pages` | A 100-page PDF must not allocate ~1.4 GB, which is what `walidapro/ocr_verifier/pdf_render.py:41-107` does today |
| `test_token_refresh_is_single_flight` | 20 concurrent page tasks at expiry → **exactly one** token request |
| `test_provider_budget_enforced_fleet_wide` | Two worker processes share one budget |
| `test_100_page_job_completes_all_callbacks` | The soak test for INVARIANT-1 |
| `test_alignment_latency_within_expected_rtf` | Against the **measured** RTF ≈ 0.20 baseline (5.42 s for 27.5 s of audio) |

## 4. The critical-for-V1 list

Ordered. **V1 must not ship without these passing.**

| # | Test | Requirement |
|---|---|---|
| 1 | `test_duplicate_jobid_inserts_once` + `test_concurrent_identical_posts_create_one_job` | REQ-IDEM-01/02 |
| 2 | **`test_page_3_fails_page_4_times_out_all_pages_still_report`** | **REQ-META-02 / INVARIANT-1** |
| 3 | `test_metadata_patched_before_any_page_upload` | REQ-META-01 |
| 4 | `test_upload_order_image_then_text` + `test_upload_order_audio_then_json` | REQ-UPL-05 |
| 5 | `test_completed_sent_only_after_both_confirm_uploads` | REQ-CB-01 |
| 6 | `test_token_refreshed_below_30s_remaining` + `test_token_refresh_is_single_flight` | REQ-AUTH-02 |
| 7 | `test_401_retried_once_after_forced_refresh` | REQ-AUTH-04 |
| 8 | `test_expired_source_url_recovers` | REQ-SRC-02 |
| 9 | `test_malformed_pdf_fails_whole_job_with_safe_reason` | REQ-CB-05 |
| 10 | **`test_worker_sigkill_mid_page_recovers`** — one terminal report per page, no duplicate confirmed upload | INVARIANT-1 |
| 11 | `test_timing_json_matches_schema` + `test_timing_monotonic_and_non_overlapping` | REQ-ART-04 |
| 12 | **`test_text_artifact_byte_stable_across_runs`** — because Job B re-tokenizes this file, drifting bytes means drifting token ids | REQ-ART-02 |
| 13 | `test_artifact_type_values_are_exact` — `Image`/`Text`/`Audio`/`JSON`, capitalized | REQ-CB-02/03 |
| 14 | `test_content_type_is_one_of_four_allowed` | REQ-UPL-04 |
| 15 | `test_postgres_down_returns_503_not_202` | honesty about acceptance |
| 16 | `test_hs256_and_alg_none_rejected` | [12-security.md](12-security.md) §2 |
| 17 | `test_source_url_host_must_be_allowlisted` | SSRF |
| 18 | `test_diacritizer_preserves_consonantal_skeleton` | the product's correctness guarantee |
| 19 | `test_anchor_ratio_on_golden_corpus_not_below_baseline` | quality regression, baseline 0.78 measured |
| 20 | `pytest src/tests` → 56 passed | the existing regression net stays green |

Numbers 2 and 10 are the ones that would actually take a production incident to discover otherwise.

## 5. Test doubles

### The contract fake

A single small FastAPI app, `tests/fakes/backend/`, implementing all five Backend endpoints plus a
presigned-upload stand-in and a Keycloak `/token` + `/certs` pair. It is the highest-leverage piece of
test infrastructure in the plan, because the entire V1 risk is protocol conformance.

It must be able to inject, on demand:

| Fault | Why |
|---|---|
| `401` on any endpoint | REQ-AUTH-04 |
| `403` on a presigned URL | the expiry path (REQ-SRC-02) |
| `500`/`503` on any endpoint | retry classification |
| `429` with and without `Retry-After` | budget-aware retry |
| A slow response (configurable delay) | timeout handling |
| A duplicate `confirm-upload` or `completed` | `Q-02`, `Q-03` — so we learn our own behaviour even before the Backend answers |
| An expiring upload ticket | fresh-ticket path |

It should also **record and assert call order**, since four of the twenty critical tests are ordering
assertions.

### Provider stubs

| Default suite | Nightly suite |
|---|---|
| All AI providers stubbed. Golden responses recorded from real calls, stored in `tests/golden/`. **Zero paid calls, zero dollars, zero quota.** | An opt-in job (`-m paid`) that runs one real page through each provider to catch contract drift — a changed response shape, a deprecated model. |

**Keep the existing structural enforcement.** `src/tests/test_ocr_resumability.py:52-54` monkeypatches
functions so that they *raise* if reached. That is stronger than a mock's call assertion: it makes an
accidental network call a test failure rather than a slow test. Every new stub follows the same pattern.

### Golden data

- Real page records, JPEGs and WAVs from `work/` — 186 records, 119 images, 95 audio files already
  exist. Use `work/فلفل المغرور-lighton/p003.json` and `work/النجوم/p003.json` as the primary
  fixtures; between them they cover the box-bearing and box-free variants and both TTS engines.
- A small multi-page PDF fixture. `src/tests/conftest.py`'s `tiny_pdf` already builds a real 3-page PDF
  with `fitz` — reuse it rather than committing a binary.
- **Arabic filenames and content in fixtures are deliberate.** The real corpus has directory names
  containing Arabic text, spaces, `?` and `' - Copy'`; encoding bugs in this system will be Arabic
  encoding bugs, and a fixture set of ASCII filenames would hide them.

## 6. Quality gates and CI

There is no CI today. What must run on every commit:

| Gate | Tool | Blocking |
|---|---|---|
| Format + lint | `ruff format --check`, `ruff check` | ✔ |
| **Import-layer contract** | `import-linter` against the rules in [16-repository-structure.md](16-repository-structure.md) §2 | ✔ — this is what keeps the API from reaching a model |
| Type check | `mypy ai_engine/` (strict for `domain/` and `ports/`, lenient for adapters wrapping untyped legacy code) | ✔ |
| Unit + integration + API + AI tests | `pytest tests/ src/tests/` | ✔ |
| **The existing 56 tests** | `pytest src/tests` | ✔ |
| Coverage on `ai_engine/` | `pytest --cov=ai_engine` — **PROPOSAL** 80% line, 100% on `domain/` and `application/` | ✔ |
| E2E against the contract fake | `pytest tests/e2e` | ✔ |
| Dependency audit | `pip-audit` | warn in V1 |
| Performance guards | `pytest tests/performance` | nightly |
| Real-provider contract check | `pytest -m paid` | nightly, opt-in |

Two notes on coverage targets: 100% on `domain/` is cheap because it is pure stdlib code with no I/O, and
it is worth it because that is where INVARIANT-1's predicates live. Adapters are explicitly *not* held
to a high bar — they are thin wrappers over untested legacy code, and chasing coverage there produces
mock-heavy tests that assert the implementation rather than the behaviour.
