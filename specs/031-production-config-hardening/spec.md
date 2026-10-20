# Feature Specification: Safe production configuration

**Feature:** `031-production-config-hardening` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P1 before any deployment outside a dev machine

## Problem

Several settings ship with dev-friendly defaults (`service/docs/07-status-and-roadmap.md` § Known gaps):
- `AUTH_ENFORCE=false`: the service trusts any caller (waits on Q-01).
- `SOURCE_URL_ALLOWED_HOSTS` empty: logs a warning, and then any https host is allowed.
- `PROVIDER_CALL_BUDGETS` empty: nothing meters paid calls.
- (walidapro) `.env.example` ships `AI_ENGINE_AUTH_DISABLED=true` and CORS `*`, which shouldn't be copied.

## User Story 1 — Production refuses to start unsafe (P1)

**Given** `ENVIRONMENT=production`, **When** any of these is unset or unsafe, **Then** the
process exits at startup with a message naming the setting: `AUTH_ENFORCE` not true;
`SOURCE_URL_ALLOWED_HOSTS` empty; `PROVIDER_CALL_BUDGETS` empty while a paid engine is
configured; `JOB_STORE=memory`.

## User Story 2 — Dev stays easy (P2)

`ENVIRONMENT=dev` (default) keeps today's behaviour, with warnings.

## Requirements

- **FR-001** `ENVIRONMENT` setting: `dev | staging | production`.
- **FR-002** Startup validation in `config/settings.py` (pydantic model validator).
- **FR-003** `/ready` reports config-safety status.
- **FR-004** Answer Q-01 (token issuer, `azp`) so `AUTH_ENFORCE=true` can be configured.

## Success Criteria

- **SC-001** Tests: each unsafe combination in production fails startup; dev starts.
