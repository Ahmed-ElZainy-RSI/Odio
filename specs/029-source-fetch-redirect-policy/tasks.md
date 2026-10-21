# Tasks: redirect policy

- [ ] T001 Ask the Backend/storage owner whether presigned URLs ever redirect. **Still open.** Built the conservative answer (refuse all) meanwhile; if the answer is yes, the change is to follow at most one hop and re-run `validate_source_url` on the target (documented in the module docstring)
- [x] T002 [P] Tests — `service/tests/unit/test_source_fetcher_redirects.py` (18): 301/302/303/307/308 × {metadata IP, RFC1918, same host} refused, target never requested, no file saved, no presigned refresh; refused even when the httpx client is configured to follow; the reason leaks neither the Location nor the URL signature; a plain 200 still fetches
- [x] T003 `follow_redirects=False` passed on every source request; any 3xx → `SourceInvalidError` ("source download redirected (HTTP 302); redirects are refused"), logged as `source.redirect_refused` with the target host only
- [x] T004 Removed the "No redirect policy" row from `service/docs/07-status-and-roadmap.md` § Known gaps

Found while implementing: a 3xx used to be treated as **success** (status < 400). The redirect's body was saved as `source.bin` and failed later as "empty" or "not a PDF", a misleading reason. It now fails at the right place with the right reason.

Code choice: the existing `SOURCE_INVALID` (non-retryable) instead of a new `SOURCE_REDIRECT_REFUSED`. A retry cannot fix a redirect, and adding a 16th code would change the error taxonomy the Backend sees.

Verification (2026-10-21, vx2lm): service 663 passed including slow (was 645); `ruff` clean.
Branch: `feature/029-source-fetch-redirect-policy`, merged into `dev`.
