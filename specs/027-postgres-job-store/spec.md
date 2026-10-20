# Feature Specification: Postgres job store (multi-host)

**Feature:** `027-postgres-job-store` · **Created:** 2026-10-20 · **Status:** Draft
**Priority:** P2 (roadmap `T-4.1` proper)

## Problem

The durable store is SQLite in WAL mode: one host, one writer. It is fine for one API and N
workers on a single machine. Running workers on two hosts needs a shared store.
`service/ai_engine/adapters/persistence/sql.py::POSTGRES_DELTA` already records the four
differences.

## User Story 1 — Workers on several hosts share one queue (P2)

**Given** `JOB_STORE=postgres` and `DATABASE_URL`, **When** two worker hosts run, **Then**
each job and page is claimed by exactly one worker (lease semantics unchanged), and every
INVARIANT-1 and durability test passes unchanged.

## Requirements

- **FR-001** Postgres adapter implementing the same `JobStore` port, plus the budget, outbox, cache (021) and usage (025) tables.
- **FR-002** Claims use `SELECT … FOR UPDATE SKIP LOCKED`.
- **FR-003** Migrations via Alembic (the `sql` extra already lists it).
- **FR-004** `tests/unit/test_job_store.py` parametrized over `[memory, sqlite, postgres]`. Postgres runs in CI via a service container.

## Success Criteria

- **SC-001** All 46 store tests and the e2e fault matrix pass on Postgres.
- **SC-002** A 2-worker-process concurrency test shows no double claims.
