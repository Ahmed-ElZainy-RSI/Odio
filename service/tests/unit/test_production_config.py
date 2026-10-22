"""Spec 031: production refuses to start with settings that are only safe on a laptop."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.api.app import create_app
from ai_engine.config.settings import Settings
from ai_engine.observability.metrics import Metrics

SAFE = {
    "environment": "production",
    "auth_enforce": True,
    "backend_allowed_client_ids": ["reading-first-backend"],
    "source_url_allowed_hosts": ["storage.reading-first.example"],
    "source_url_resolve_and_check": True,
    "job_store": "sqlite",
    "provider_call_budgets": ["gemini=1000/86400"],
    "backend_base_url": "https://backend.reading-first.example",
    "token_url": "https://auth.reading-first.example/realms/rf/protocol/openid-connect/token",
}


def test_a_safe_production_configuration_starts() -> None:
    assert Settings(**SAFE).config_problems() == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"auth_enforce": False}, "AUTH_ENFORCE"),
        ({"source_url_allowed_hosts": []}, "SOURCE_URL_ALLOWED_HOSTS"),
        ({"source_url_resolve_and_check": False}, "SOURCE_URL_RESOLVE_AND_CHECK"),
        ({"job_store": "memory"}, "JOB_STORE=memory"),
        ({"provider_call_budgets": []}, "PROVIDER_CALL_BUDGETS"),
        ({"backend_base_url": "https://localhost:6060"}, "BACKEND_BASE_URL"),
        (
            {"token_url": "http://127.0.0.1:9090/realms/rf/protocol/openid-connect/token"},
            "TOKEN_URL",
        ),
    ],
)
def test_each_unsafe_setting_refuses_production(change: dict, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        Settings(**(SAFE | change))


def test_all_problems_are_reported_at_once() -> None:
    with pytest.raises(ValueError) as info:
        Settings(environment="production")
    message = str(info.value)
    for fragment in (
        "AUTH_ENFORCE",
        "SOURCE_URL_ALLOWED_HOSTS",
        "PROVIDER_CALL_BUDGETS",
        "TOKEN_URL",
    ):
        assert fragment in message


def test_budgets_are_not_required_when_nothing_paid_is_configured() -> None:
    free = SAFE | {
        "provider_call_budgets": [],
        "ocr_engine": "textlayer",
        "tts_engine": "synthetic",
    }
    assert Settings(**free).config_problems() == []


@pytest.mark.parametrize("environment", ["dev", "staging"])
def test_dev_and_staging_start_with_the_defaults(environment: str) -> None:
    settings = Settings(environment=environment)
    assert len(settings.config_problems()) >= 4  # reported, not fatal


def test_an_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="environment"):
        Settings(environment="prod")


def test_ready_reports_unsafe_settings_without_failing_a_dev_instance() -> None:
    app = create_app(
        settings=Settings(environment="dev"), store=InMemoryJobStore(), metrics=Metrics()
    )
    body = TestClient(app).get("/ready").json()
    config = body["checks"]["config"]
    assert config["ok"] is True
    assert "unsafe for production" in config["detail"] and "AUTH_ENFORCE" in config["detail"]


def test_ready_says_production_safe_when_it_is() -> None:
    app = create_app(settings=Settings(**SAFE), store=InMemoryJobStore(), metrics=Metrics())
    assert (
        TestClient(app).get("/ready").json()["checks"]["config"]["detail"]
        == "production: production-safe"
    )
