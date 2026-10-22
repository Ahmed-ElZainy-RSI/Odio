# walidapro vs service: contract differences

Every scenario in `walidapro/tests/test_ai_contract.py` (13 tests), its counterpart in
`service/`, and who follows the V1 spec (`assets/Backend & AI integration V1.pdf`).
Literal-body replays live in `service/tests/behaviour/test_walidapro_scenarios.py`.

## Differences

| # | Topic | walidapro | service | The spec says | Disposition |
|---|---|---|---|---|---|
| D1 | `jobId` format | any string (`"job-basic-1"`) | RFC 4122 GUID, else `422` | `jobId : "<GUID>"` | **service follows the spec**; keep |
| D2 | `folderId`, `pathPrefix` | optional (omitted in most tests) | required, else `422` | listed in every request body; the upload cycle needs both | **service follows the spec**; keep |
| D3 | Unknown `jobType` | `400` | `422` | silent | design choice → **Q-18** |
| D4 | Audio job without `pageNumber` | `400` | `422` | silent | design choice → **Q-18** |
| D5 | `sourceDownloadUrl` | any URL (`"http://x"`) | https + allowlisted host, else `422` | `"<pdf s3 url>"`, nothing more | spec silent; service stricter for SSRF (029, 031); keep |
| D6 | Same `jobId`, different body | `202 duplicate: true` | `409` (source URL excluded from the comparison) | "if it has it then it should **return success**" | **walidapro follows the spec's letter.** Reopened as **Q-17**; service keeps `409` until the Backend answers |
| D7 | Response body | `{jobId, duplicate}` | `{jobId, status, duplicate, acceptedAt}` | `202 accepted`, no body defined | compatible (superset) |

## Agreements, mapped

| walidapro test | Same behaviour in service |
|---|---|
| `test_enqueue_returns_202` | `tests/api/test_jobs.py::test_valid_job_a_returns_202`; replay `test_a_new_job_is_202_and_not_a_duplicate` |
| `test_enqueue_same_jobid_twice_is_idempotent` | `test_duplicate_job_id_returns_202_and_flags_the_duplicate`, `test_duplicate_job_id_does_not_execute_twice`; replay `test_the_identical_request_twice_is_202_both_times_and_flags_the_duplicate` |
| `test_enqueue_unknown_job_type_rejected` | rejected (422 not 400, D3): `test_unknown_job_type_is_rejected` |
| `test_audio_job_requires_page_number` | rejected (422 not 400, D4): `test_page_number_required_for_job_b` |
| `test_debug_get_unknown_job_404` | `test_job_status_404_for_unknown_job`; replay `test_debug_get_of_an_unknown_job_is_404` |
| `test_missing_authorization_header_rejected` | `tests/api/test_auth_enforced.py::test_no_authorization_header_is_401` (with enforcement on) |
| `test_malformed_bearer_token_rejected` | `tests/api/test_auth_enforced.py::test_a_malformed_authorization_header_is_401`, plus token validation in `tests/unit/test_auth_jwks.py` |
| `test_pages_extraction_job_full_flow` (metadata `{pagesCount}` once, `completed` with `Image`+`Text`, no `failed`) | `tests/e2e/test_job_a_end_to_end.py::test_job_a_completes_every_page`, `test_metadata_is_sent_before_any_upload`; `tests/e2e/test_job_a_failure_paths.py::test_artifact_types_in_completed_are_exact_strings` |
| `test_pages_extraction_job_download_url_refresh_on_expiry` (403 → `get-download-url` → retry) | `tests/e2e/test_job_a_failure_paths.py::test_expired_source_url_is_recovered` |
| `test_pages_extraction_job_page_error_reported_as_failed` (one page fails, others continue, reason carries the cause) | `test_one_failed_page_does_not_stop_the_others`, `test_failure_reason_is_safe_and_code_prefixed` |
| `test_audio_timing_job_full_flow` (`completed` with `Audio`+`JSON`) | `tests/e2e/test_job_b_end_to_end.py::test_job_b_completes_and_reports_the_page` |

Not in walidapro, covered only in service: metadata delivery gating page work, the callback
outbox, the reconciler, crash-resume without re-uploading, provider budgets, retry with
`Retry-After`, SSRF controls, the 42-case fault matrix.

One note on walidapro's own tests: `test_pages_extraction_job_page_error_reported_as_failed`
expects the job status `"done"` after a page failure. In service a job with failed pages
still reaches a terminal state with one callback per page (INVARIANT-1), so the observable
contract matches.

## For the Backend team

Two answers needed: **Q-17** (duplicate GUID with a different body) and **Q-18** (400 vs
422). Both are in `docs/backend-v1/22-open-questions.md`. Everything else is either
identical or the service follows the spec where walidapro doesn't.
