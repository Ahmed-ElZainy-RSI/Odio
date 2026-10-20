# Implementation Plan: AI Engine Service

**Feature:** `003-ai-engine-service` · **Date:** 2026-10-20 · **Spec:** [spec.md](spec.md)
**Detailed design:** `docs/backend-v1/05-target-architecture.md` … `21-implementation-roadmap.md`; ADRs in `19-adrs.md`.

## Summary

Hexagonal service, two process roles (api, worker) + reconciler, one image. The API does one
indexed insert; the worker claims jobs and runs page tasks through ports whose adapters wrap
`src/pipeline`. SQLite in Postgres shape is the durable store, queue, outbox and budget.

## Technical context

| | |
|---|---|
| Language | Python ≥ 3.11 |
| Dependencies | FastAPI, pydantic v2 + pydantic-settings, httpx, PyJWT[crypto], structlog, tenacity; extras `ai`, `sql`, `metrics`, `dev` |
| Storage | SQLite WAL (`BEGIN IMMEDIATE` claims); Postgres adapter pending (`POSTGRES_DELTA`) |
| Testing | pytest + hypothesis + respx; contract fake Backend (`tests/fakes/backend.py`) |
| Deploy | `service/Dockerfile` (multi-stage, non-root), `docker-compose.yml`, CI `.github/workflows/service-ci.yml` |

## Constitution check

| Principle | Status | Note |
|---|---|---|
| I | ⚠️ | Fast tier has no correction gate in-service; `002` is offline/human |
| II | ✅ | `token_id(page, index)` pure → Job B reproduces Job A ids |
| III | ✅ | No fabricated boxes; untimed tokens explicit |
| IV | ✅ | TTS duration gate, bind report → `anchor_ratio` |
| V | ✅ | Page ledger, one terminal report per page, outbox, reconciler |
| VI | ✅ | `OCR_ENGINE=auto` |
| VII | ✅ | Fleet-wide provider budget; verified tier off by default |
| VIII | ✅ | |
| IX | ✅ | `test_domain_purity.py` |
| X | ✅ | `service/docs/07-status-and-roadmap.md` known-gaps table |

## Project structure

```
service/
  ai_engine/
    api/            app, routers (jobs, ops), schemas, deps, errors
    application/    accept_job, run_extraction_job, run_audio_job, run_page_task, worker_loop, outbox, reconcile
    domain/         enums, errors, tokens, job, invariants, budget, callbacks   (stdlib only)
    ports/          ai, backend, job_store, rate_budget
    adapters/       ai/ (renderer, textlayer, ocr_providers, tts, aligner, legacy), auth/, backend/, http/retry, persistence/, storage/
    artifacts/      tokenizer, text_builder, timing_builder
    observability/  logging, metrics, context
    config/settings.py · main.py · worker.py
  tests/ unit/ api/ e2e/ behaviour/ fakes/
  devtools/ fake_backend_server.py, local_stack.sh
```

Contract: [contracts/post-api-jobs.md](contracts/post-api-jobs.md).
