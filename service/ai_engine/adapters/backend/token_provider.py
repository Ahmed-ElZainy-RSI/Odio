"""Keycloak `client_credentials` token provider.

Implements the V1 spec's helper exactly:

  * POST the token endpoint with ``grant_type=client_credentials`` and the client id/secret
    as ``application/x-www-form-urlencoded``.
  * Cache the token **in process memory only** -- the spec explicitly says "no need to
    store such a token in db or other place".
  * Reuse it while more than 30 s of its 300 s life remain ("5 minutes minus 30 sec").
  * Treat 401 as unauthorized-or-expired.

Two things the spec leaves to us, both of which matter under real concurrency:

  * **Single-flight refresh.** With `WORKER_PAGE_CONCURRENCY` page tasks in flight, an
    expiry would otherwise trigger N simultaneous token requests. One lock turns that
    thundering herd into one request.
  * **A 401 from Keycloak itself is never retried.** It means the credentials are wrong,
    which is a configuration failure that must page a human rather than back off.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ai_engine.adapters.http.retry import RetryPolicy, send_with_retry
from ai_engine.domain.errors import AuthenticationError, ErrorCode
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import TOKEN_REFRESH, Metrics

log = get_logger(__name__)

#: Refresh once fewer than this many seconds of life remain. The spec's "5 minutes minus
#: 30 sec" for a 300 s token.
REFRESH_MARGIN_SECONDS = 30


class KeycloakTokenProvider:
    """In-memory, single-flight bearer token cache."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        client: httpx.AsyncClient,
        metrics: Metrics,
        policy: RetryPolicy | None = None,
        clock: object = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client
        self._metrics = metrics
        self._policy = policy or RetryPolicy(max_attempts=3)
        self._clock = clock  # injectable for tests
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _now(self) -> float:
        return float(self._clock())  # type: ignore[operator]

    @property
    def _is_fresh(self) -> bool:
        return self._token is not None and self._now() < self._expires_at

    async def token(self) -> str:
        if self._is_fresh:
            return self._token  # type: ignore[return-value]

        async with self._lock:
            # Re-check inside the lock: a concurrent caller may already have refreshed,
            # which is the entire point of single-flight.
            if self._is_fresh:
                return self._token  # type: ignore[return-value]
            return await self._refresh()

    async def invalidate(self) -> None:
        """Force the next :meth:`token` call to refresh. Called after a 401."""
        async with self._lock:
            self._token = None
            self._expires_at = 0.0

    async def _refresh(self) -> str:
        async def send() -> httpx.Response:
            return await self._client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="keycloak.token",
                code=ErrorCode.AUTHENTICATION_ERROR,
                # Deliberately no on_unauthorized: a 401 here is bad credentials.
            )
        except Exception as exc:
            self._metrics.inc(TOKEN_REFRESH, {"outcome": "failure"})
            raise AuthenticationError(f"could not obtain a Backend token: {exc}") from exc

        if response.status_code >= 400:
            self._metrics.inc(TOKEN_REFRESH, {"outcome": "failure"})
            raise AuthenticationError(f"token endpoint returned HTTP {response.status_code}")

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            self._metrics.inc(TOKEN_REFRESH, {"outcome": "failure"})
            raise AuthenticationError("token endpoint response had no access_token")

        expires_in = float(payload.get("expires_in", 300))
        self._token = str(token)
        self._expires_at = self._now() + max(0.0, expires_in - REFRESH_MARGIN_SECONDS)
        self._metrics.inc(TOKEN_REFRESH, {"outcome": "success"})
        # Never log the token, not even truncated.
        log.info("token.refreshed", expires_in=expires_in, margin=REFRESH_MARGIN_SECONDS)
        return self._token
