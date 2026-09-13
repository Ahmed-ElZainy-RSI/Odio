"""Inbound token validation, against a fake Keycloak realm with real RSA keys.

Generating real keys rather than mocking `jwt.decode` matters here: the failure modes this
suite guards against -- algorithm confusion, an unrotated `kid`, a token signed by the
wrong key -- only exist at the cryptographic layer. A mocked decoder would pass all of
them.
"""

from __future__ import annotations

import json
import time
import warnings
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ai_engine.adapters.auth.jwks_verifier import JwksTokenVerifier, _derive_issuer
from ai_engine.domain.errors import AuthenticationError, AuthorizationError

ISSUER = "https://kc.test/realms/reading-first"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
BACKEND_CLIENT = "reading-first-backend"


class FakeRealm:
    """A Keycloak stand-in: holds keys, serves a JWKS, and mints tokens."""

    def __init__(self) -> None:
        self._keys: dict[str, rsa.RSAPrivateKey] = {}
        self.jwks_requests = 0
        self.add_key("kid-1")

    def add_key(self, kid: str) -> None:
        self._keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def private_key(self, kid: str) -> rsa.RSAPrivateKey:
        return self._keys[kid]

    def drop_key(self, kid: str) -> None:
        self._keys.pop(kid, None)

    def jwks(self) -> dict:
        keys = []
        for kid, private in self._keys.items():
            numbers = private.public_key().public_numbers()
            keys.append(
                {
                    "kty": "RSA",
                    "kid": kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64uint(numbers.n),
                    "e": _b64uint(numbers.e),
                }
            )
        return {"keys": keys}

    def mint(
        self,
        *,
        kid: str = "kid-1",
        header_kid: str | None = None,
        algorithm: str = "RS256",
        claims: dict[str, Any] | None = None,
        key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        """Mint a token. `header_kid` lets the header claim a kid we did not sign with,
        which is how an unknown-kid (key rotation) scenario is simulated."""
        now = int(time.time())
        payload = {
            "iss": ISSUER,
            "azp": BACKEND_CLIENT,
            "sub": "service-account",
            "iat": now,
            "exp": now + 300,
        }
        payload.update(claims or {})
        signing_key = key or self._keys[kid]
        return jwt.encode(
            payload,
            signing_key,  # type: ignore[arg-type]
            algorithm=algorithm,
            headers={"kid": header_kid or kid},
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.jwks_requests += 1
            return httpx.Response(200, json=self.jwks())

        return httpx.MockTransport(handler)


def _b64uint(value: int) -> str:
    import base64

    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(
    realm: FakeRealm,
    *,
    transport: httpx.BaseTransport | None = None,
    allowed: list[str] | None = None,
    algorithms: list[str] | None = None,
    clock: Clock | None = None,
    audience: str | None = None,
    **kwargs: Any,
) -> tuple[JwksTokenVerifier, Clock]:
    tick = clock or Clock()
    client = httpx.AsyncClient(transport=transport or realm.transport())
    verifier = JwksTokenVerifier(
        jwks_url=JWKS_URL,
        issuer=ISSUER,
        allowed_client_ids=allowed if allowed is not None else [BACKEND_CLIENT],
        algorithms=["RS256"] if algorithms is None else algorithms,
        client=client,
        audience=audience,
        clock=tick,
        **kwargs,
    )
    return verifier, tick


@pytest.fixture
def realm() -> FakeRealm:
    return FakeRealm()


# ── The happy path ──────────────────────────────────────────────────────────────


async def test_a_valid_token_is_accepted(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    claims = await verifier.verify(realm.mint())
    assert claims["azp"] == BACKEND_CLIENT
    assert claims["iss"] == ISSUER


async def test_jwks_is_cached_across_calls(realm: FakeRealm) -> None:
    """Fetching the realm's keys per request would be a small denial of service."""
    verifier, _ = build(realm)
    for _ in range(5):
        await verifier.verify(realm.mint())
    assert realm.jwks_requests == 1


async def test_cache_expires_after_the_ttl(realm: FakeRealm) -> None:
    clock = Clock()
    verifier, _ = build(realm, clock=clock, cache_ttl_seconds=100)
    await verifier.verify(realm.mint())
    clock.now += 200
    await verifier.verify(realm.mint())
    assert realm.jwks_requests == 2


# ── Algorithm confusion ─────────────────────────────────────────────────────────


async def test_hs256_token_is_rejected(realm: FakeRealm) -> None:
    """The classic algorithm-confusion attack.

    If HS256 were accepted alongside a JWKS verifier, the *public* key would double as a
    valid HMAC secret -- so anyone who can read the published JWKS could mint tokens.
    """
    verifier, _ = build(realm)
    with warnings.catch_warnings():
        # A deliberately short HMAC secret: the point is that the algorithm is refused
        # before the key is ever considered.
        warnings.simplefilter("ignore")
        forged = jwt.encode(
            {"iss": ISSUER, "azp": BACKEND_CLIENT, "exp": int(time.time()) + 300},
            "any-shared-secret",
            algorithm="HS256",
            headers={"kid": "kid-1"},
        )
    with pytest.raises(AuthenticationError, match="not permitted"):
        await verifier.verify(forged)


async def test_alg_none_token_is_rejected(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    unsigned = jwt.encode(
        {"iss": ISSUER, "azp": BACKEND_CLIENT, "exp": int(time.time()) + 300},
        key="",
        algorithm=None,
    )
    with pytest.raises(AuthenticationError, match="not permitted"):
        await verifier.verify(unsigned)


def test_constructing_with_a_symmetric_algorithm_is_refused(realm: FakeRealm) -> None:
    """Fail at construction, not at request time."""
    with pytest.raises(ValueError, match="symmetric"):
        build(realm, algorithms=["HS256"])
    with pytest.raises(ValueError, match="symmetric"):
        build(realm, algorithms=["RS256", "none"])


def test_an_empty_algorithm_list_is_refused(realm: FakeRealm) -> None:
    with pytest.raises(ValueError, match="at least one"):
        build(realm, algorithms=[])


async def test_an_algorithm_outside_the_configured_set_is_rejected(
    realm: FakeRealm,
) -> None:
    verifier, _ = build(realm, algorithms=["RS512"])
    with pytest.raises(AuthenticationError, match="not permitted"):
        await verifier.verify(realm.mint(algorithm="RS256"))


# ── Signature, issuer, expiry ───────────────────────────────────────────────────


async def test_a_token_signed_by_a_foreign_key_is_rejected(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = realm.mint(key=attacker_key)
    with pytest.raises(AuthenticationError, match="signature"):
        await verifier.verify(forged)


async def test_an_expired_token_is_rejected(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    stale = realm.mint(claims={"exp": int(time.time()) - 3600})
    with pytest.raises(AuthenticationError, match="expired"):
        await verifier.verify(stale)


async def test_expiry_leeway_tolerates_small_clock_skew(realm: FakeRealm) -> None:
    """Two servers are never perfectly in sync; a 1-second skew must not reject traffic."""
    verifier, _ = build(realm, leeway_seconds=60)
    barely_stale = realm.mint(claims={"exp": int(time.time()) - 5})
    claims = await verifier.verify(barely_stale)
    assert claims["azp"] == BACKEND_CLIENT


async def test_a_not_yet_valid_token_is_rejected(realm: FakeRealm) -> None:
    verifier, _ = build(realm, leeway_seconds=0)
    future = realm.mint(claims={"nbf": int(time.time()) + 3600})
    with pytest.raises(AuthenticationError, match="not yet valid"):
        await verifier.verify(future)


async def test_a_token_with_no_expiry_is_rejected(realm: FakeRealm) -> None:
    """A token that never expires is a permanent credential in a log somewhere."""
    verifier, _ = build(realm)
    now = int(time.time())
    forever = jwt.encode(
        {"iss": ISSUER, "azp": BACKEND_CLIENT, "iat": now},
        realm.private_key("kid-1"),
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )
    with pytest.raises(AuthenticationError, match="required claim"):
        await verifier.verify(forever)


async def test_a_foreign_issuer_is_rejected(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    other = realm.mint(claims={"iss": "https://evil.test/realms/other"})
    with pytest.raises(AuthenticationError, match="issuer"):
        await verifier.verify(other)


# ── Authorization: 401 vs 403 ───────────────────────────────────────────────────


async def test_an_unlisted_client_gets_403_not_401(realm: FakeRealm) -> None:
    """The distinction matters: 401 tells the caller to refresh, which would loop forever."""
    verifier, _ = build(realm, allowed=["some-other-client"])
    with pytest.raises(AuthorizationError, match="not permitted"):
        await verifier.verify(realm.mint())


async def test_an_empty_allowlist_accepts_any_valid_client(realm: FakeRealm) -> None:
    """Documented behaviour of the object. The settings layer is what forbids this
    combination in production, by refusing to start with AUTH_ENFORCE and no allowlist."""
    verifier, _ = build(realm, allowed=[])
    claims = await verifier.verify(realm.mint(claims={"azp": "anyone"}))
    assert claims["azp"] == "anyone"


async def test_identity_falls_back_to_aud_when_azp_is_absent(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    token = realm.mint(claims={"azp": None, "aud": BACKEND_CLIENT})
    claims = await verifier.verify(token)
    assert claims["aud"] == BACKEND_CLIENT


async def test_a_list_audience_is_matched_against_the_allowlist(realm: FakeRealm) -> None:
    verifier, _ = build(realm)
    token = realm.mint(claims={"azp": None, "aud": ["account", BACKEND_CLIENT]})
    assert await verifier.verify(token)


async def test_audience_is_verified_when_configured(realm: FakeRealm) -> None:
    verifier, _ = build(realm, audience="ai-engine")
    with pytest.raises(AuthenticationError, match="audience"):
        await verifier.verify(realm.mint(claims={"aud": "something-else"}))


async def test_audience_is_not_required_by_default(realm: FakeRealm) -> None:
    """Keycloak client_credentials tokens do not reliably carry `aud`."""
    verifier, _ = build(realm)
    assert await verifier.verify(realm.mint())


# ── Key rotation ────────────────────────────────────────────────────────────────


async def test_an_unknown_kid_triggers_one_refresh_and_then_succeeds(
    realm: FakeRealm,
) -> None:
    """A realm that rotates keys must not cause an outage."""
    verifier, _ = build(realm)
    await verifier.verify(realm.mint())  # warms the cache
    assert realm.jwks_requests == 1

    realm.add_key("kid-2")
    claims = await verifier.verify(realm.mint(kid="kid-2"))
    assert claims["azp"] == BACKEND_CLIENT
    assert realm.jwks_requests == 2


async def test_refreshes_are_rate_limited(realm: FakeRealm) -> None:
    """Otherwise bogus `kid`s become an amplification vector against Keycloak."""
    clock = Clock()
    verifier, _ = build(realm, clock=clock, min_refresh_interval_seconds=60)
    await verifier.verify(realm.mint())
    before = realm.jwks_requests

    for _ in range(10):
        with pytest.raises(AuthenticationError, match="unknown key"):
            await verifier.verify(realm.mint(header_kid="kid-does-not-exist"))

    assert realm.jwks_requests - before <= 1, "refresh was not rate-limited"


async def test_rate_limit_lifts_after_the_interval(realm: FakeRealm) -> None:
    clock = Clock()
    verifier, _ = build(realm, clock=clock, min_refresh_interval_seconds=60)
    await verifier.verify(realm.mint())
    realm.add_key("kid-2")

    clock.now += 120
    assert await verifier.verify(realm.mint(kid="kid-2"))


# ── Failing closed ──────────────────────────────────────────────────────────────


async def test_an_unreachable_jwks_endpoint_rejects_every_request(
    realm: FakeRealm,
) -> None:
    """A verifier that fails open is worse than no verifier: it looks like protection."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("realm unreachable")

    verifier, _ = build(realm, transport=httpx.MockTransport(handler))
    with pytest.raises(AuthenticationError, match="unavailable"):
        await verifier.verify(realm.mint())


async def test_a_5xx_from_the_jwks_endpoint_rejects(realm: FakeRealm) -> None:
    verifier, _ = build(realm, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(AuthenticationError, match="unavailable"):
        await verifier.verify(realm.mint())


async def test_a_malformed_jwks_document_rejects(realm: FakeRealm) -> None:
    verifier, _ = build(
        realm,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"not json")),
    )
    with pytest.raises(AuthenticationError, match="unusable"):
        await verifier.verify(realm.mint())


async def test_an_empty_key_set_rejects(realm: FakeRealm) -> None:
    verifier, _ = build(
        realm,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=json.dumps({"keys": []}).encode())
        ),
    )
    with pytest.raises(AuthenticationError, match="no usable signing keys"):
        await verifier.verify(realm.mint())


@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b", "a.b.c.d", "....", "Bearer x.y.z"])
async def test_malformed_tokens_are_rejected(realm: FakeRealm, garbage: str) -> None:
    verifier, _ = build(realm)
    with pytest.raises(AuthenticationError):
        await verifier.verify(garbage)


# ── Issuer derivation ───────────────────────────────────────────────────────────


def test_issuer_is_derived_from_the_token_url() -> None:
    assert _derive_issuer(f"{ISSUER}/protocol/openid-connect/token") == ISSUER


def test_issuer_derivation_returns_none_for_an_unexpected_shape() -> None:
    assert _derive_issuer("https://kc.test/oauth/token") is None
