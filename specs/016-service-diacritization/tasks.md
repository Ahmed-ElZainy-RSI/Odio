# Tasks: service diacritization

- [x] T001 [P] `SyntheticDiacritizer` (fatha after each letter: free, deterministic) and adapter unit tests: marks-only output applied by position; a dropped word or a changed letter leaves the page untouched (review 1.0); a model crash leaves it untouched; a `TypeError` is not swallowed (007); weights load once per instance; a missing package → `ModelUnavailableError`
- [x] T002 [P] Page-task tests: undiacritised page → diacritised Text artifact; a page printed with tashkeel is kept (the model isn't even called); a diacritizer that changes a letter is ignored (`outcome=rejected`); verification runs **before** diacritisation (the diacritizer sees `الفصل`, not `الفضل`); Job B still re-derives Job A's token ids from the diacritised text. `service/tests/unit/test_diacritizer_local.py` (17)
- [x] T003 `adapters/ai/diacritizer_local.py`: `LocalDiacritizer` (model owned by the instance, not a module global; `load()` for fail-fast; same rules as `tashkel.py` plus the 006 letter guard)
- [x] T004 Page task `_diacritize()` after `_verify()`: routes on `diacritic_ratio` (new in `domain/preservation.py`, identical to `extract.diacritic_ratio`, synced to the CLI copy) against the existing `DIAC_THRESHOLD` (0.15), and re-checks the port's invariant (same ids, same letters) instead of trusting the adapter
- [x] T005 Setting `DIACRITIZER=none|local` (validated at startup) + metric `ai_engine_diacritization_total{outcome=applied|kept|review|rejected}`; documented in `service/docs/03-configuration.md`
- [x] T006 Packaging: `diacritize` extra in `pyproject.toml` (it pulls in torch, so it's not part of `ai`); Dockerfile `--build-arg INSTALL_DIACRITIZER=1`. **Not build-verified**: the local Docker has no `build --check` and a full build needs network. The `RUN` is a guarded no-op by default

Decision: **opt-in (`DIACRITIZER=none` by default)**, mirroring `ENABLE_VERIFIED_TIER`. With it on by default, every undiacritised page would fail in any environment without the model (CI, slim images). Instead, `DIACRITIZER=local` with a missing model makes the **worker refuse to start**, once, loudly. Production enables it explicitly.

Also fixed: `_MARKS_RE` in `domain/preservation.py` had been stored as literal combining characters since 006 (the file tool decoded the `\u` escapes). It was correct but unreadable. It's now written as escapes, after asserting the code points were exactly the intended ones.

Verification (2026-10-21, vx2lm): service 719 passed including slow (was 702); `src/tests` 159; system Python 138 + 13 skipped; `ruff` clean.
Branch: `feature/016-service-diacritization`, merged into `dev`.
