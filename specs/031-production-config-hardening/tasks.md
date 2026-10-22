# Tasks: production config hardening

- [ ] T001 Get Q-01 answered (token issuer, `azp`). **Still open, but no longer blocking the code:** it only decides the *values* for `AUTH_ENFORCE`/`BACKEND_ALLOWED_CLIENT_IDS`. Until then production simply won't start, which is the intended behaviour
- [x] T002 [P] Tests — `service/tests/unit/test_production_config.py` (15): a safe production config starts; each of the 7 unsafe settings refuses production on its own; all problems are reported at once; budgets aren't required when nothing paid is configured; dev/staging start with the defaults; an unknown environment is rejected; `/ready` shows the problems without failing a dev instance and says "production-safe" when it is
- [x] T003 `ENVIRONMENT` (`dev`|`staging`|`production`, default `dev`), `Settings.config_problems()`, and a model validator that refuses production listing every problem. Checks: `AUTH_ENFORCE` off, `SOURCE_URL_ALLOWED_HOSTS` empty, `SOURCE_URL_RESOLVE_AND_CHECK` off, `JOB_STORE=memory`, paid OCR/TTS without `PROVIDER_CALL_BUDGETS`, `BACKEND_BASE_URL`/`TOKEN_URL` on localhost (the shipped defaults)
- [x] T004 `/ready` gains `checks.config` (informational, `ok: true`: a dev instance must stay in rotation); the API logs `config.unsafe_for_production` at boot outside production
- [x] T005 Docs: `ENVIRONMENT` row in `service/docs/03-configuration.md`, a production section in `service/.env.example`, and the three known-gap rows in `07-status-and-roadmap.md` note that production now refuses those defaults

Design choice: dev stays the default so local runs, tests and the CI container smoke test need no new configuration. The protection is that a production deployment has to say so, and then can't run unsafely.

Verification (2026-10-22, vx2lm): service 776 passed including slow (was 761); `ruff` clean; locks current.
Branch: `feature/031-production-config-hardening`, merged into `dev`.
