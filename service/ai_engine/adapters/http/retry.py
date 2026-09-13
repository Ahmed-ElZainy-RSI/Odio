"""One retry policy for every outbound call.

Three retrying layers exist in this system and they MULTIPLY: the Backend retries the job
(the V1 spec says so explicitly), we retry the operation, and provider SDKs retry the HTTP
call. The legacy code already demonstrates the pathology --
``walidapro/ocr_verifier/final_context_reviewer.py`` retries 3x at the application level
while never overriding the OpenAI SDK's default ``max_retries=2``, for up to **9 billed
completions** and ~30 minutes of worst-case latency for one page. Add the Backend's retry
and it is 27.

So the rules here are deliberately conservative:

1. Own retries at exactly one layer, and set every SDK's own count to 0.
2. Prefer idempotency over aggression -- the Backend re-delivers anyway.
3. Never retry a deterministic rejection; it only burns budget and quota.
4. Always jitter. ``walidapro/ocr_verifier/lighton_ocr.py:197`` is
   ``time.sleep(2 ** attempt)`` with no jitter, so N workers failing together retry in
   lockstep, and its worst case is 546 s of blocked worker.
5. Bound the *total* time, not just the attempt count. An attempt cap alone lets a slow
   endpoint hold a worker far past any useful deadline.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import StrEnum

import httpx

from ai_engine.domain.errors import ErrorCode
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)

#: Retry these. 408/429 are explicit back-pressure; 5xx and transport errors are transient
#: by definition.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509})

#: Never retry these. They are deterministic rejections: the same request will be rejected
#: again, so a retry buys nothing and costs quota.
TERMINAL_STATUS = frozenset(
    {
        400,
        401,
        402,
        403,
        404,
        405,
        406,
        409,
        410,
        411,
        412,
        413,
        414,
        415,
        416,
        417,
        418,
        422,
        426,
        428,
        431,
        451,
        501,
        505,
    }
)


class Disposition(StrEnum):
    """What to do with one attempt's outcome."""

    SUCCEED = "succeed"
    RETRY = "retry"
    REFRESH_AND_RETRY = "refresh_and_retry"
    TERMINAL = "terminal"


def classify(
    *, status: int | None, transport_error: bool, can_refresh: bool, already_refreshed: bool
) -> Disposition:
    """Decide an attempt's disposition. Pure, so it is directly unit-testable.

    Keeping this separate from the send loop is deliberate: the classification table is the
    part that is easy to get subtly wrong, and it is the part
    ``walidapro/ocr_verifier/lighton_ocr.py:180`` gets wrong by retrying an auth failure
    identically to a 429.
    """
    if transport_error:
        return Disposition.RETRY
    assert status is not None
    if status < 400:
        return Disposition.SUCCEED
    if status == 401 and can_refresh and not already_refreshed:
        # The spec's rule: a 401 means "unauthorized token or expired". Refresh once. A
        # second 401 means the credentials are wrong, not stale -- that must surface.
        return Disposition.REFRESH_AND_RETRY
    if status in RETRYABLE_STATUS:
        return Disposition.RETRY
    return Disposition.TERMINAL


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 60.0
    #: Wall-clock ceiling across all attempts, including backoff.
    total_budget: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")

    def backoff_ceiling(self, attempt: int) -> float:
        """The un-jittered ceiling for this attempt. Exponential, capped."""
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        return min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Exponential backoff with FULL jitter, i.e. uniform in ``[0, ceiling]``.

        Full jitter rather than equal jitter, because the failure mode we care about is N
        workers hitting the same provider outage and then retrying in lockstep.
        """
        ceiling = self.backoff_ceiling(attempt)
        source = rng or random
        return source.uniform(0.0, ceiling)


def retry_after_seconds(response: httpx.Response, *, now: float | None = None) -> float | None:
    """Honour a server-supplied `Retry-After`, in either RFC 7231 form.

    A rate limiter telling us when to come back is strictly better information than our own
    backoff guess, so it wins when present. Both the delay-seconds and the HTTP-date form
    are accepted, because real gateways emit both.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from datetime import UTC, datetime

        target = parsedate_to_datetime(raw)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        reference = datetime.fromtimestamp(now, tz=UTC) if now is not None else datetime.now(UTC)
        return max(0.0, (target - reference).total_seconds())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RetryStats:
    """What actually happened. Attached to the exception and available to callers."""

    attempts: int
    refreshed: bool
    slept: float


class RetryExhausted(Exception):
    """All attempts failed, or the budget ran out. Carries the last outcome."""

    def __init__(
        self,
        *,
        attempts: int,
        last_status: int | None,
        last_error: str,
        code: ErrorCode,
        exhausted_budget: bool = False,
        stats: RetryStats | None = None,
    ) -> None:
        self.attempts = attempts
        self.last_status = last_status
        self.last_error = last_error
        self.code = code
        self.exhausted_budget = exhausted_budget
        self.stats = stats or RetryStats(attempts=attempts, refreshed=False, slept=0.0)
        why = "budget exhausted" if exhausted_budget else f"{attempts} attempts"
        super().__init__(f"{code.value} after {why}: {last_error}")


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    policy: RetryPolicy,
    operation: str,
    code: ErrorCode,
    on_unauthorized: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] | None = None,
    rng: random.Random | None = None,
) -> httpx.Response:
    """Send with the shared policy.

    ``sleep``, ``clock`` and ``rng`` are injectable so retry behaviour can be tested
    deterministically and without real waiting -- otherwise a retry test suite either sleeps
    for real or asserts nothing.
    """
    monotonic = clock or asyncio.get_event_loop().time
    started = monotonic()
    last_status: int | None = None
    last_error = "no attempt made"
    refreshed = False
    slept = 0.0
    attempt = 0

    def remaining() -> float:
        return policy.total_budget - (monotonic() - started)

    while attempt < policy.max_attempts:
        attempt += 1
        transport_error = False
        response: httpx.Response | None = None
        try:
            response = await send()
        except httpx.HTTPError as exc:
            transport_error = True
            last_status, last_error = None, f"{type(exc).__name__}: {exc}"
            log.warning(
                "http.transport_error",
                operation=operation,
                attempt=attempt,
                detail=last_error,
            )
        else:
            last_status = response.status_code
            last_error = f"HTTP {response.status_code}"

        disposition = classify(
            status=last_status,
            transport_error=transport_error,
            can_refresh=on_unauthorized is not None,
            already_refreshed=refreshed,
        )

        if disposition is Disposition.SUCCEED:
            assert response is not None
            return response

        if disposition is Disposition.REFRESH_AND_RETRY:
            refreshed = True
            log.info("http.unauthorized.refreshing", operation=operation)
            assert on_unauthorized is not None
            await on_unauthorized()
            continue  # immediate retry, no backoff -- the token was simply stale

        if disposition is Disposition.TERMINAL:
            if response is not None:
                log.warning(
                    "http.terminal_status", operation=operation, status=response.status_code
                )
                return response  # the caller inspects; retrying would be pointless
            break

        # Disposition.RETRY
        if attempt >= policy.max_attempts:
            break

        explicit = retry_after_seconds(response) if response is not None else None
        delay = (
            min(explicit, policy.max_delay)
            if explicit is not None
            else policy.delay_for(attempt, rng=rng)
        )
        if explicit is not None:
            log.info("http.retry_after", operation=operation, attempt=attempt, seconds=delay)

        if delay > remaining():
            log.warning(
                "http.budget_exhausted",
                operation=operation,
                attempt=attempt,
                budget=policy.total_budget,
            )
            raise RetryExhausted(
                attempts=attempt,
                last_status=last_status,
                last_error=last_error,
                code=code,
                exhausted_budget=True,
                stats=RetryStats(attempts=attempt, refreshed=refreshed, slept=slept),
            )

        await sleep(delay)
        slept += delay

    raise RetryExhausted(
        attempts=attempt,
        last_status=last_status,
        last_error=last_error,
        code=code,
        stats=RetryStats(attempts=attempt, refreshed=refreshed, slept=slept),
    )
