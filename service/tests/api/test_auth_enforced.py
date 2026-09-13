"""`POST /api/jobs` with inbound authentication turned on.

`AUTH_ENFORCE=false` is the default while `Q-01` is unresolved, so these tests are what
prove the enforced path actually works when the Backend team answers. They use real RSA
keys against a fake realm, so the whole chain runs: header parsing, JWKS fetch, signature
verification, claim checks, and the 401-vs-403 distinction.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.auth.jwks_verifier import JwksTokenVerifier
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.api.app import create_app
from ai_engine.config.settings import Settings
from ai_engine.observability.metrics import Metrics
from tests.conftest import ALLOWED_HOST, job_a_body
from tests.unit.test_auth_jwks import BACKEND_CLIENT, ISSUER, JWKS_URL, FakeRealm


@pytest.fixture
def realm() -> FakeRealm:
    return FakeRealm()


def enforced_client(realm: FakeRealm, *, allowed: list[str] | None = None) -> TestClient:
    settings = Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="CRITICAL",
        source_url_allowed_hosts=[ALLOWED_HOST],
        auth_enforce=True,
        backend_allowed_client_ids=allowed or [BACKEND_CLIENT],
    )
    app = create_app(settings=settings, store=InMemoryJobStore(), metrics=Metrics())
    # Swap in a verifier bound to the fake realm rather than reaching the network.
    app.state.token_verifier = JwksTokenVerifier(
        jwks_url=JWKS_URL,
        issuer=ISSUER,
        allowed_client_ids=allowed or [BACKEND_CLIENT],
        algorithms=["RS256"],
        client=httpx.AsyncClient(transport=realm.transport()),
    )
    return TestClient(app)


def post(client: TestClient, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/jobs", json=job_a_body(uuid4()), headers=headers)


# ── Accepted ────────────────────────────────────────────────────────────────────


def test_a_valid_token_is_accepted(realm: FakeRealm) -> None:
    with enforced_client(realm) as client:
        response = post(client, realm.mint())
    assert response.status_code == 202


def test_the_job_still_runs_through_the_normal_path(realm: FakeRealm) -> None:
    with enforced_client(realm) as client:
        body = post(client, realm.mint()).json()
    assert body["status"] == "QUEUED"
    assert body["duplicate"] is False


# ── Rejected: 401, "we cannot establish who you are" ────────────────────────────


def test_no_authorization_header_is_401(realm: FakeRealm) -> None:
    with enforced_client(realm) as client:
        response = post(client)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"


@pytest.mark.parametrize("header", ["", "Token abc", "Basic dXNlcjpwYXNz", "bearer", "Bearer"])
def test_a_malformed_authorization_header_is_401(realm: FakeRealm, header: str) -> None:
    with enforced_client(realm) as client:
        response = client.post(
            "/api/jobs", json=job_a_body(uuid4()), headers={"Authorization": header}
        )
    assert response.status_code == 401


def test_an_expired_token_is_401(realm: FakeRealm) -> None:
    import time

    with enforced_client(realm) as client:
        response = post(client, realm.mint(claims={"exp": int(time.time()) - 3600}))
    assert response.status_code == 401


def test_a_token_signed_by_a_foreign_key_is_401(realm: FakeRealm) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with enforced_client(realm) as client:
        response = post(client, realm.mint(key=attacker))
    assert response.status_code == 401


def test_an_hs256_token_is_401(realm: FakeRealm) -> None:
    """Algorithm confusion, at the HTTP boundary."""
    import time
    import warnings

    import jwt

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        forged = jwt.encode(
            {"iss": ISSUER, "azp": BACKEND_CLIENT, "exp": int(time.time()) + 300},
            "secret",
            algorithm="HS256",
            headers={"kid": "kid-1"},
        )
    with enforced_client(realm) as client:
        response = post(client, forged)
    assert response.status_code == 401


def test_a_foreign_issuer_is_401(realm: FakeRealm) -> None:
    with enforced_client(realm) as client:
        response = post(client, realm.mint(claims={"iss": "https://evil.test/realms/x"}))
    assert response.status_code == 401


# ── Rejected: 403, "we know who you are, and you may not" ───────────────────────


def test_a_valid_token_from_an_unlisted_client_is_403(realm: FakeRealm) -> None:
    """Not 401: a 401 tells the caller to refresh, which would loop forever."""
    with enforced_client(realm, allowed=["some-other-service"]) as client:
        response = post(client, realm.mint())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTHORIZATION_ERROR"


# ── Failing closed ──────────────────────────────────────────────────────────────


def test_enforcement_without_a_verifier_rejects_everything() -> None:
    """A verifier that fails open is worse than none: it looks like protection.

    This is the configuration mistake most likely to happen in a hurry -- flipping
    AUTH_ENFORCE on before the verifier is wired.
    """
    settings = Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="CRITICAL",
        source_url_allowed_hosts=[ALLOWED_HOST],
        auth_enforce=True,
        backend_allowed_client_ids=[BACKEND_CLIENT],
    )
    app = create_app(settings=settings, store=InMemoryJobStore(), metrics=Metrics())
    app.state.token_verifier = None  # the mistake

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json=job_a_body(uuid4()),
            headers={"Authorization": "Bearer anything"},
        )
    assert response.status_code == 401


def test_an_unreachable_realm_rejects_rather_than_admitting(realm: FakeRealm) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("realm down")

    settings = Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="CRITICAL",
        source_url_allowed_hosts=[ALLOWED_HOST],
        auth_enforce=True,
        backend_allowed_client_ids=[BACKEND_CLIENT],
    )
    app = create_app(settings=settings, store=InMemoryJobStore(), metrics=Metrics())
    app.state.token_verifier = JwksTokenVerifier(
        jwks_url=JWKS_URL,
        issuer=ISSUER,
        allowed_client_ids=[BACKEND_CLIENT],
        algorithms=["RS256"],
        client=httpx.AsyncClient(transport=httpx.MockTransport(unreachable)),
    )
    with TestClient(app) as client:
        response = post(client, realm.mint())
    assert response.status_code == 401


# ── Probes stay open ────────────────────────────────────────────────────────────


def test_health_and_ready_do_not_require_a_token(realm: FakeRealm) -> None:
    """A probe that needs a credential is a probe that fails during an auth incident."""
    with enforced_client(realm) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200


def test_ready_reports_auth_as_configured_when_enforced(realm: FakeRealm) -> None:
    with enforced_client(realm) as client:
        checks = client.get("/ready").json()["checks"]
    assert checks["inbound_auth"]["ok"] is True
    assert "Q-01" not in (checks["inbound_auth"]["detail"] or "")
