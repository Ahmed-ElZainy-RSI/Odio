# Tasks: provider call cache + atomic writes

- [x] T001 [P] Key tests: stable and order-independent; any change to provider/model/operation/any param is a miss; the format version is part of the key; missing identity refused
- [x] T002 [P] Atomic-write test: an `os.replace` failure mid-write leaves the old entry and no temp file
- [x] T003 Port `ports/cache.py` (`ProviderCache`), key `domain/cache_key.py` (`fingerprint`, `digest`, `CACHE_FORMAT`), filesystem adapter `adapters/persistence/cache_fs.py` (atomic tmp+rename, optional max age, an unreadable entry is a miss, keys can't escape the root)
- [ ] T004 SQL adapter in the job-store DB. **Deferred to 027 (Postgres)**: a shared cache only matters for a multi-host fleet; the filesystem cache is correct on one host or a shared volume
- [x] T005 Atomic writes on the CLI side are already done: `records.write_record` (009) and `failures.py` (008) both write tmp + rename. No separate `atomic.py` needed
- [x] T006 Wrappers `adapters/ai/cached.py` (`CachedOcrEngine`, `CachedTts`) around any engine implementing the port, keeping `name`/`provider`/`model_id` (the budget and the verified tier read them). Only validated results are stored (the TTS duration gate has passed; exceptions are never cached). OCR keys include the page number because token ids embed it; TTS keys include the voice (the engine default when none is given). Text goes into keys only as a digest. Wired in `worker.py` for **paid** engines only, opt-in via `PROVIDER_CACHE_DIR`; metric `ai_engine_provider_cache_total{operation,outcome}`; documented in `service/docs/03-configuration.md`. Tests: `service/tests/unit/test_provider_cache.py` (30)

Known limitation: a cache hit still consumes one provider-budget slot, because the page task charges the budget before OCR runs. Conservative (never over quota). Refunding on a hit needs a change to the budget flow; tracked here.

Not in scope: caching in the CLI pipeline. It already resumes per page by status (008/009), so a re-run doesn't repeat finished pages.

Verification (2026-10-21, vx2lm): service 749 passed including slow (was 719); `src/tests` 159; `ruff` clean.
Branch: `feature/021-provider-call-cache`, merged into `dev`.
