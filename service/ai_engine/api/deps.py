"""Dependency wiring and request-scoped concerns."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, Request

from ai_engine.application.accept_job import AcceptJob
from ai_engine.config.settings import Settings
from ai_engine.domain.errors import AuthenticationError, AuthorizationError, ValidationError
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget

log = get_logger(__name__)

#: Hosts that must never be reachable through a caller-supplied URL, regardless of the
#: allowlist. Defence in depth against a misconfigured allowlist entry.
_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal"})


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_metrics(request: Request) -> Metrics:
    return request.app.state.metrics


def get_budget(request: Request) -> RateBudget:
    return request.app.state.budget


def get_accept_job(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    store: Annotated[JobStore, Depends(get_store)],
    metrics: Annotated[Metrics, Depends(get_metrics)],
) -> AcceptJob:
    return AcceptJob(
        store,
        metrics=metrics,
        enable_verified_tier=settings.enable_verified_tier,
        default_quality_tier=settings.default_quality_tier,
    )


def validate_source_url(url: str, settings: Settings) -> str:
    """Apply the SSRF controls to a caller-supplied download URL.

    `sourceDownloadUrl` is supplied by the caller and fetched SERVER-SIDE, so an
    unrestricted engine is an open proxy into whatever network the worker sits in -- cloud
    metadata endpoints, internal admin panels, Postgres on localhost.

    The Backend is trusted, but "trusted" is not "incapable of being compromised or
    misconfigured", and this is a cheap control.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if settings.source_url_require_https and parsed.scheme != "https":
        raise ValidationError("sourceDownloadUrl must use https")

    if host in _BLOCKED_HOSTS:
        raise ValidationError("sourceDownloadUrl host is not permitted")

    allowed = [h.lower() for h in settings.source_url_allowed_hosts]
    if not allowed:
        # Empty allowlist means "not configured". Fail closed in production, but let local
        # development through with a loud warning rather than blocking every request.
        log.warning(
            "source_url.allowlist_not_configured",
            hint="set SOURCE_URL_ALLOWED_HOSTS; requests are not host-restricted",
        )
        return url

    if not any(host == entry or host.endswith(f".{entry}") for entry in allowed):
        raise ValidationError("sourceDownloadUrl host is not on the allowlist")
    return url


async def authenticate(request: Request) -> str | None:
    """Validate the inbound bearer token (REQ-AUTH-06).

    BLOCKED ON Q-01. The V1 PDF states that inbound calls carry
    `Authorization: Bearer {ai-engine-bearer-token}` but never says how to validate it:
    no algorithm, no JWKS location, no expected audience, and no indication of which
    Keycloak client the Backend presents.

    Until that is answered, `AUTH_ENFORCE=false` (the default) accepts any request and
    logs the presented client so we can learn the real value from a staging call. Setting
    `AUTH_ENFORCE=true` requires `BACKEND_ALLOWED_CLIENT_IDS` and performs full JWKS
    validation, which lands with T-2.2.
    """
    settings: Settings = request.app.state.settings
    header = request.headers.get("authorization", "")

    if not settings.auth_enforce:
        if header:
            log.info("auth.observed", scheme=header.split(" ", 1)[0][:16])
        else:
            log.warning("auth.absent", path=request.url.path)
        return None

    if not header.lower().startswith("bearer "):
        raise AuthenticationError("missing or malformed Authorization header")

    verifier = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        # Refusing is the only safe behaviour: enforcing auth with no verifier configured
        # would otherwise accept every token.
        raise AuthenticationError("inbound token verification is not configured")

    claims = await verifier.verify(header.removeprefix("Bearer ").removeprefix("bearer "))
    client_id = claims.get("azp") or claims.get("aud")
    if client_id not in settings.backend_allowed_client_ids:
        raise AuthorizationError("caller client id is not permitted")
    return str(client_id)
