"""The Backend's job progress APIs -- the four PATCH callbacks.

This client only knows how to *send* one envelope. Deciding what to send, persisting the
intent first, and retrying until it lands are the outbox drainer's job
(:mod:`ai_engine.application.drain_outbox`).
"""

from __future__ import annotations

import httpx

from ai_engine.adapters.http.retry import RetryExhausted, RetryPolicy, send_with_retry
from ai_engine.domain.callbacks import CallbackEnvelope
from ai_engine.domain.errors import CallbackFailedError, ErrorCode
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import CALLBACK_ATTEMPTS, Metrics
from ai_engine.ports.backend import TokenProvider

log = get_logger(__name__)


class BackendJobsClient:
    """Sends one progress callback."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient,
        tokens: TokenProvider,
        metrics: Metrics,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._tokens = tokens
        self._metrics = metrics
        # Fewer in-call attempts than the uploader: the outbox retries across calls, so
        # being aggressive here would multiply with that.
        self._policy = policy or RetryPolicy(max_attempts=3)

    async def send(self, envelope: CallbackEnvelope) -> None:
        path = envelope.path()
        body = envelope.body()

        async def send_once() -> httpx.Response:
            return await self._client.patch(
                f"{self._base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {await self._tokens.token()}"},
            )

        try:
            response = await send_with_retry(
                send_once,
                policy=self._policy,
                operation=f"backend.{envelope.kind.value}",
                code=ErrorCode.CALLBACK_FAILED,
                on_unauthorized=self._tokens.invalidate,
            )
        except RetryExhausted as exc:
            self._metrics.inc(
                CALLBACK_ATTEMPTS, {"endpoint": envelope.kind.value, "outcome": "failure"}
            )
            raise CallbackFailedError(
                f"{envelope.kind.value} could not be delivered after {exc.attempts} "
                f"attempts ({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            self._metrics.inc(
                CALLBACK_ATTEMPTS, {"endpoint": envelope.kind.value, "outcome": "failure"}
            )
            raise CallbackFailedError(f"{envelope.kind.value} returned HTTP {response.status_code}")

        self._metrics.inc(
            CALLBACK_ATTEMPTS, {"endpoint": envelope.kind.value, "outcome": "success"}
        )
        log.info(
            "callback.delivered",
            kind=envelope.kind.value,
            page_number=envelope.page_number,
        )
