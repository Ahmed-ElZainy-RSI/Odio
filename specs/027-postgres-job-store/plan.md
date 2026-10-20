# Plan: Postgres job store

**Spec:** [spec.md](spec.md) · **Guide:** `service/docs/06-extending.md` § Worked example: the Postgres store

```
service/ai_engine/adapters/persistence/postgres.py   new (psycopg 3)
service/ai_engine/adapters/persistence/factory.py    JOB_STORE=postgres
service/migrations/                                  alembic env + initial revision
service/tests/unit/test_job_store.py                 add to STORES when DATABASE_URL set
.github/workflows/service-ci.yml                     postgres service container job
service/docker-compose.yml                           optional postgres profile
```

Apply each entry in `POSTGRES_DELTA` explicitly, and delete it from the delta once done.
