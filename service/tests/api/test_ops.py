"""Health, readiness and metrics endpoints."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.api.app import create_app
from ai_engine.config.settings import Settings
from tests.conftest import job_a_body


def test_health_is_always_200(client: TestClient) -> None:
    """Liveness performs no dependency checks.

    Checking one here would restart every container during a brief Postgres outage, which
    is strictly worse than serving 503 from /ready.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_per_dependency(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["job_store"]["ok"] is True


def test_ready_is_503_when_the_store_is_broken() -> None:
    class BrokenStore(InMemoryJobStore):
        async def get(self, job_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("connection refused")

    app = create_app(
        settings=Settings(ai_engine_json_logs=False, ai_engine_log_level="CRITICAL"),
        store=BrokenStore(),
    )
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert response.json()["checks"]["job_store"]["ok"] is False


def test_ready_flags_unenforced_auth(client: TestClient) -> None:
    """The Q-01 gap must be visible in readiness output, not buried."""
    checks = client.get("/ready").json()["checks"]
    assert "Q-01" in (checks["inbound_auth"]["detail"] or "")


def test_metrics_endpoint_serves_an_exposition(client: TestClient) -> None:
    client.post("/api/jobs", json=job_a_body(uuid4()))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "ai_engine_jobs_accepted_total" in response.text


def test_openapi_document_is_generated(client: TestClient) -> None:
    """The OpenAPI document is the machine contract and must come from the code."""
    schema = client.get("/openapi.json").json()
    assert "/api/jobs" in schema["paths"]
    assert "post" in schema["paths"]["/api/jobs"]
    responses = schema["paths"]["/api/jobs"]["post"]["responses"]
    assert "202" in responses
    assert "409" in responses


# ── /ops/budget ─────────────────────────────────────────────────────────────────


def test_budget_reports_nothing_when_no_provider_is_metered() -> None:
    """An empty list, not an error: unmetered is the default and it is fine."""
    app = create_app(
        settings=Settings(ai_engine_json_logs=False, ai_engine_log_level="CRITICAL"),
        store=InMemoryJobStore(),
    )
    with TestClient(app) as client:
        response = client.get("/ops/budget")
        assert response.status_code == 200
        assert response.json() == {"windows": []}


def test_budget_reports_the_remaining_allowance_per_provider() -> None:
    """Worth an endpoint because the failure is otherwise invisible: jobs stop making
    progress and nothing looks broken. `remaining: 0` explains it exactly."""
    from ai_engine.adapters.persistence.rate_budget import InMemoryBudget
    from ai_engine.domain.budget import ProviderLimit

    budget = InMemoryBudget({"gemini": ProviderLimit("gemini", 10, 86_400)})
    app = create_app(
        settings=Settings(ai_engine_json_logs=False, ai_engine_log_level="CRITICAL"),
        store=InMemoryJobStore(),
        budget=budget,
    )
    with TestClient(app) as client:
        window = client.get("/ops/budget").json()["windows"][0]

    assert window["provider"] == "gemini"
    assert window["maxCalls"] == 10
    assert window["remaining"] == 10
    assert window["used"] == 0
    assert window["windowReset"] > window["windowStart"]


# ── Store ownership ─────────────────────────────────────────────────────────────


def test_the_api_serves_the_configured_store_when_none_is_injected(tmp_path: Path) -> None:
    """Regression, and a bad one: `run_api` used to build the app with no store, so the
    API accepted jobs into an in-memory store that no worker could ever read. Jobs would
    return 202 and then simply never happen.

    Proven the only way that means anything -- a job POSTed to the API is readable from a
    *separate* connection to the configured database file.
    """
    import asyncio

    from ai_engine.adapters.persistence.sql import SqliteJobStore

    settings = Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="CRITICAL",
        job_store="sqlite",
        job_store_path=tmp_path / "jobs.db",
        source_url_allowed_hosts=["storage.example.com"],
        auth_enforce=False,
    )
    job_id = uuid4()
    with TestClient(create_app(settings=settings)) as client:
        assert isinstance(client.app.state.store, SqliteJobStore)
        assert client.post("/api/jobs", json=job_a_body(job_id)).status_code == 202

    async def read_it_back() -> object:
        async with SqliteJobStore(tmp_path / "jobs.db") as store:
            return await store.get(job_id)

    assert asyncio.run(read_it_back()) is not None, "a worker process could not see the job"
