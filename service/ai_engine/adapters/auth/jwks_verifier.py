"""Inbound bearer token validation against a Keycloak realm's JWKS.

`Q-01` context: the V1 spec states that inbound calls carry
``Authorization: Bearer {ai-engine-bearer-token}`` but never says how to validate it -- no
algorithm, no JWKS location, no expected audience, and no indication of which Keycloak
client the Backend presents. This is our proposal, built so the unresolved parts are
*configuration* rather than code:

* **RS256 (or another asymmetric algorithm) only.** Accepting HS256 next to a JWKS-based
  verifier is the classic algorithm-confusion vulnerability: the public key doubles as a
  valid HMAC secret, so anyone who can read the JWKS can mint tokens. The setting layer
  already refuses symmetric algorithms; this layer refuses them again.
* **`aud` is optional, `azp` is checked.** Keycloak ``client_credentials`` tokens do not
  reliably carry a predictable audience, so requiring one would reject valid tokens. The
  caller's identity is taken from ``azp`` (falling back to ``aud``) and matched against an
  allowlist.
* **No offline fallback, ever.** If no key is available, every request is rejected. A
  verifier that fails open is worse than no verifier, because it looks like protection.

The HTTP client is injected, so this is fully testable against a fake realm.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from ai_engine.domain.errors import AuthenticationError, AuthorizationError
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)

#: Asymmetric families only. Anything else is refused before a signature is checked.
ALLOWED_ALGORITHM_PREFIXES = ("RS", "ES", "PS")


@dataclass
class _JwksCache:
    keys: dict[str, PyJWK] = field(default_factory=dict)
    fetched_at: float = 0.0
    #: When a refresh last returned the SAME key set, i.e. told us nothing new. Only
    #: those are rate-limited -- a refresh that discovers a rotated key must never be
    #: blocked, or every rotation would cause an auth outage.
    last_unhelpful_refresh_at: float = 0.0


class JwksTokenVerifier:
    """Verifies a Keycloak-issued JWT and returns its claims."""

    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str | None,
        allowed_client_ids: list[str],
        algorithms: list[str],
        client: httpx.AsyncClient,
        audience: str | None = None,
        leeway_seconds: int = 30,
        cache_ttl_seconds: int = 3600,
        min_refresh_interval_seconds: int = 60,
        clock: Any = time.monotonic,
    ) -> None:
        bad = [a for a in algorithms if not a.startswith(ALLOWED_ALGORITHM_PREFIXES)]
        if bad:
            raise ValueError(f"refusing symmetric or unknown JWT algorithms: {bad}")
        if not algorithms:
            raise ValueError("at least one algorithm must be permitted")

        self._jwks_url = jwks_url
        self._issuer = issuer
        self._allowed = {c.strip() for c in allowed_client_ids if c.strip()}
        self._algorithms = list(algorithms)
        self._client = client
        self._audience = audience
        self._leeway = leeway_seconds
        self._ttl = cache_ttl_seconds
        # Rate-limits refreshes so an attacker cannot use bogus `kid`s to hammer Keycloak.
        self._min_refresh_interval = min_refresh_interval_seconds
        self._clock = clock
        self._cache = _JwksCache()

    def _now(self) -> float:
        return float(self._clock())

    # ── JWKS handling ─────────────────────────────────────────────────────────────

    @property
    def _cache_is_fresh(self) -> bool:
        return bool(self._cache.keys) and (self._now() - self._cache.fetched_at < self._ttl)

    async def _fetch_jwks(self) -> None:
        now = self._now()
        # Rate-limit only unhelpful refreshes. An attacker sending bogus `kid`s hits this
        # after one attempt; a genuine key rotation is never blocked, because the refresh
        # that discovers it returns a different key set.
        if (
            self._cache.last_unhelpful_refresh_at > 0
            and now - self._cache.last_unhelpful_refresh_at < self._min_refresh_interval
        ):
            log.info("jwks.refresh_rate_limited", url=self._jwks_url)
            return

        previous = set(self._cache.keys)

        try:
            response = await self._client.get(self._jwks_url)
        except httpx.HTTPError as exc:
            log.error("jwks.fetch_failed", detail=f"{type(exc).__name__}")
            raise AuthenticationError("token verification keys are unavailable") from exc

        if response.status_code >= 400:
            log.error("jwks.fetch_failed", status=response.status_code)
            raise AuthenticationError("token verification keys are unavailable")

        try:
            document = response.json()
            keys = {
                key["kid"]: PyJWK.from_dict(key)
                for key in document.get("keys", [])
                if key.get("kid")
            }
        except (ValueError, KeyError, TypeError) as exc:
            log.error("jwks.malformed", detail=f"{type(exc).__name__}")
            raise AuthenticationError("token verification keys are unusable") from exc

        if not keys:
            raise AuthenticationError("the realm published no usable signing keys")

        self._cache.keys = keys
        self._cache.fetched_at = now
        if set(keys) == previous:
            # Nothing new. If we are asked again soon, do not bother the realm.
            self._cache.last_unhelpful_refresh_at = now
        else:
            self._cache.last_unhelpful_refresh_at = 0.0
        log.info("jwks.refreshed", key_count=len(keys), rotated=set(keys) != previous)

    async def _signing_key(self, kid: str | None) -> PyJWK:
        if not self._cache_is_fresh:
            await self._fetch_jwks()
        if kid and kid in self._cache.keys:
            return self._cache.keys[kid]

        # An unknown kid usually means the realm rotated its keys, so re-fetch once --
        # rate-limited, so this cannot be turned into an amplification vector.
        await self._fetch_jwks()
        if kid and kid in self._cache.keys:
            return self._cache.keys[kid]

        if kid is None and len(self._cache.keys) == 1:
            return next(iter(self._cache.keys.values()))

        raise AuthenticationError("token was signed by an unknown key")

    # ── Verification ──────────────────────────────────────────────────────────────

    async def verify(self, token: str) -> dict[str, Any]:
        """Verify a token and return its claims.

        Raises:
            AuthenticationError: we cannot establish who the caller is (401).
            AuthorizationError: we know who they are, and they are not allowed (403).
        """
        if not token or token.count(".") != 2:
            raise AuthenticationError("token is not a well-formed JWT")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("token header could not be read") from exc

        algorithm = header.get("alg", "")
        if algorithm == "none" or not algorithm.startswith(ALLOWED_ALGORITHM_PREFIXES):
            # Refused before any signature work: an attacker choosing the algorithm is the
            # whole vulnerability class.
            log.warning("auth.algorithm_refused", alg=algorithm)
            raise AuthenticationError(f"token algorithm {algorithm!r} is not permitted")
        if algorithm not in self._algorithms:
            raise AuthenticationError(f"token algorithm {algorithm!r} is not permitted")

        signing_key = await self._signing_key(header.get("kid"))

        options: dict[str, Any] = {"require": ["exp"]}
        decode_kwargs: dict[str, Any] = {
            "algorithms": self._algorithms,
            "leeway": self._leeway,
        }
        if self._issuer:
            decode_kwargs["issuer"] = self._issuer
        if self._audience:
            decode_kwargs["audience"] = self._audience
        else:
            # Keycloak client_credentials tokens do not reliably carry `aud`; requiring one
            # would reject valid tokens. Identity comes from `azp` instead.
            options["verify_aud"] = False

        try:
            claims = jwt.decode(token, signing_key.key, options=options, **decode_kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token has expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise AuthenticationError("token is not yet valid") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError("token issuer is not trusted") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError("token audience is not accepted") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError("token signature is invalid") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise AuthenticationError(f"token is missing a required claim: {exc}") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("token is invalid") from exc

        caller = claims.get("azp") or claims.get("aud")
        if isinstance(caller, list):
            caller = next((c for c in caller if c in self._allowed), caller[0] if caller else None)

        if self._allowed and caller not in self._allowed:
            # 403, not 401: we established who they are. Returning 401 would send the
            # caller into a pointless token-refresh loop.
            log.warning("auth.client_not_allowed", caller_client_id=str(caller))
            raise AuthorizationError("caller is not permitted to enqueue jobs")

        log.info("auth.verified", caller_client_id=str(caller))
        return claims


def build_verifier(settings: Any, client: httpx.AsyncClient) -> JwksTokenVerifier:
    """Construct a verifier from settings. Called by the app factory."""
    return JwksTokenVerifier(
        jwks_url=settings.resolved_jwks_url,
        issuer=getattr(settings, "jwt_issuer", None) or _derive_issuer(settings.token_url),
        allowed_client_ids=list(settings.backend_allowed_client_ids),
        algorithms=list(settings.jwt_algorithms),
        client=client,
        audience=getattr(settings, "jwt_audience", None) or None,
        leeway_seconds=settings.jwt_clock_leeway_seconds,
    )


def _derive_issuer(token_url: str) -> str | None:
    """Keycloak's issuer is the realm URL, i.e. the token URL minus the protocol suffix."""
    marker = "/protocol/openid-connect/token"
    if token_url.endswith(marker):
        return token_url[: -len(marker)]
    return None
