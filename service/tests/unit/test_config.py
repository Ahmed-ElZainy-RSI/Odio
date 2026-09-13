"""Configuration behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_engine.config.settings import Settings, assert_no_dotenv_on_disk
from ai_engine.domain.enums import QualityTier


def test_defaults_are_safe() -> None:
    """The two security-relevant defaults must be the conservative ones."""
    settings = Settings()
    # Verified tier off: it is 65-200 paid calls/page against fast's 1-2.
    assert settings.enable_verified_tier is False
    assert settings.default_quality_tier is QualityTier.FAST
    # No GPU anywhere in V1.
    assert settings.align_whisper_device == "cpu"


def test_secret_is_not_exposed_in_repr() -> None:
    settings = Settings(ai_engine_client_secret="super-secret-value")
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in str(settings)
    assert settings.ai_engine_client_secret.get_secret_value() == "super-secret-value"


def test_symmetric_jwt_algorithms_are_rejected() -> None:
    """Accepting HS256 next to a JWKS verifier is the algorithm-confusion vulnerability."""
    with pytest.raises(ValidationError, match="asymmetric"):
        Settings(jwt_algorithms=["HS256"])
    with pytest.raises(ValidationError, match="asymmetric"):
        Settings(jwt_algorithms=["RS256", "none"])


def test_enforced_auth_requires_a_client_allowlist() -> None:
    """Enforcing auth with an empty allowlist would accept every valid token."""
    with pytest.raises(ValidationError, match="backend_allowed_client_ids"):
        Settings(auth_enforce=True, backend_allowed_client_ids=[])


def test_enforced_auth_with_allowlist_is_accepted() -> None:
    settings = Settings(auth_enforce=True, backend_allowed_client_ids=["reading-first-backend"])
    assert settings.backend_allowed_client_ids == ["reading-first-backend"]


def test_csv_env_values_are_split() -> None:
    settings = Settings(source_url_allowed_hosts="a.example.com, b.example.com")
    assert settings.source_url_allowed_hosts == ["a.example.com", "b.example.com"]


def test_relative_urls_are_rejected() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        Settings(backend_base_url="localhost:6060")


def test_jwks_url_is_derived_from_the_token_endpoint() -> None:
    settings = Settings(
        token_url="https://kc.example.com/realms/reading-first/protocol/openid-connect/token"
    )
    assert settings.resolved_jwks_url.endswith(
        "/realms/reading-first/protocol/openid-connect/certs"
    )


def test_explicit_jwks_url_wins() -> None:
    settings = Settings(jwks_url="https://kc.example.com/custom/certs")
    assert settings.resolved_jwks_url == "https://kc.example.com/custom/certs"


def test_require_secrets_names_the_missing_key() -> None:
    with pytest.raises(RuntimeError, match="AI_ENGINE_CLIENT_SECRET"):
        Settings(ai_engine_client_secret="").require_secrets()


def test_require_secrets_passes_when_configured() -> None:
    Settings(ai_engine_client_secret="x").require_secrets()


def test_dotenv_on_disk_is_refused(tmp_path: Path) -> None:
    """A `.env` beside the service would silently shadow injected secrets.

    The legacy modules call `load_dotenv()` at import in 11 places, so this is a real
    override path rather than a hypothetical one.
    """
    (tmp_path / ".env").write_text("GEMINI_API_KEY=leaked\n")
    with pytest.raises(RuntimeError, match="refusing to start"):
        assert_no_dotenv_on_disk(tmp_path)


def test_no_dotenv_is_fine(tmp_path: Path) -> None:
    assert_no_dotenv_on_disk(tmp_path)


# ── Environment-sourced parsing ─────────────────────────────────────────────────
#
# These exercise Settings THROUGH os.environ rather than the constructor. That
# distinction matters: pydantic-settings JSON-decodes env values for complex types before
# field validators run, so a constructor-only test suite happily passes while
# `SOURCE_URL_ALLOWED_HOSTS=a.example.com` crashes the real process at boot. That is
# exactly the bug these tests exist to prevent recurring.


def test_csv_list_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_URL_ALLOWED_HOSTS", "storage.example.com, s3.example.com")
    monkeypatch.setenv("BACKEND_ALLOWED_CLIENT_IDS", "reading-first-backend")
    settings = Settings()
    assert settings.source_url_allowed_hosts == ["storage.example.com", "s3.example.com"]
    assert settings.backend_allowed_client_ids == ["reading-first-backend"]


def test_json_array_list_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_URL_ALLOWED_HOSTS", '["a.example.com", "b.example.com"]')
    assert Settings().source_url_allowed_hosts == ["a.example.com", "b.example.com"]


def test_single_value_list_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHMS", "RS512")
    assert Settings().jwt_algorithms == ["RS512"]


def test_malformed_json_array_is_reported_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_URL_ALLOWED_HOSTS", '["unterminated')
    with pytest.raises(ValidationError, match="does not parse"):
        Settings()


def test_scalar_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ENGINE_PORT", "9199")
    monkeypatch.setenv("ENABLE_VERIFIED_TIER", "true")
    monkeypatch.setenv("WORKER_PAGE_CONCURRENCY", "8")
    settings = Settings()
    assert settings.ai_engine_port == 9199
    assert settings.enable_verified_tier is True
    assert settings.worker_page_concurrency == 8


def test_symmetric_algorithm_from_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JWT_ALGORITHMS", "HS256")
    with pytest.raises(ValidationError, match="asymmetric"):
        Settings()


# ── Logging redaction, end to end through structlog ─────────────────────────────


def test_log_records_redact_secrets_but_keep_routes(capsys: pytest.CaptureFixture) -> None:
    """Exercise the real structlog pipeline, not just the redact() helper."""
    import json as _json

    from ai_engine.observability.logging import configure_logging, get_logger

    configure_logging(level="INFO", json_logs=True, role="api")
    get_logger("t").info(
        "request.failed",
        path="/api/jobs",
        source_download_url="https://s3.example.com/x?X-Amz-Signature=deadbeef",
        detail="token eyJhbGciOiJSUzI1NiJ9.abcdefghijkl failed",
    )
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = _json.loads(line)

    assert record["event"] == "request.failed"
    assert record["path"] == "/api/jobs"  # route survives
    assert record["source_download_url"] == "[REDACTED]"  # credential dropped by key
    assert "eyJhbGciOiJSUzI1NiJ9" not in record["detail"]  # JWT scrubbed from free text


# ── SSRF: the control that has to be on by default ──────────────────────────────


def test_the_ssrf_resolve_check_is_on_by_default() -> None:
    """It was previously hardcoded OFF in the composition root.

    The API's allowlist runs at accept time against the *literal* host string; the worker
    fetches later, over real HTTP. A hostname that is on the allowlist but resolves to
    169.254.169.254 is caught by nothing else.
    """
    assert Settings().source_url_resolve_and_check is True


def test_the_composed_worker_applies_the_resolve_check_by_default() -> None:
    """Asserted through the composition root, because that is where it was lost.

    A unit test of `validate_source_url` cannot see this: the bug was that the composed
    worker passed `resolve_and_check=False` into a policy whose own logic was correct.
    """
    import asyncio

    import httpx

    from ai_engine.adapters.persistence.memory import InMemoryJobStore
    from ai_engine.observability.metrics import Metrics
    from ai_engine.worker import build_worker

    async def build():
        settings = Settings(
            ai_engine_json_logs=False,
            ai_engine_log_level="CRITICAL",
            source_url_allowed_hosts=["storage.example.com"],
        )
        async with httpx.AsyncClient() as client:
            worker = build_worker(
                settings=settings,
                store=InMemoryJobStore(),
                metrics=Metrics(),
                http_client=client,
            )
            return worker.extraction._fetcher._policy

    policy = asyncio.run(build())
    assert policy.resolve_and_check is True
    assert policy.require_https is True
    assert policy.allowed_hosts == ("storage.example.com",)


def test_the_resolve_check_can_be_turned_off_for_a_loopback_fake() -> None:
    """Local development needs the escape hatch, and the app warns loudly when it is used."""
    assert Settings(source_url_resolve_and_check=False).source_url_resolve_and_check is False
