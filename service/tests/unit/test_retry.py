"""Retry behaviour.

The retry layer is the one place where a subtle mistake costs real money: three layers
retry in this system and they multiply. So this suite is deliberately exhaustive, and it
runs with an injected clock, sleep and RNG so nothing actually waits and every assertion is
deterministic.
"""

from __future__ import annotations

import random

import httpx
import pytest

from ai_engine.adapters.http.retry import (
    RETRYABLE_STATUS,
    TERMINAL_STATUS,
    Disposition,
    RetryExhausted,
    RetryPolicy,
    classify,
    retry_after_seconds,
    send_with_retry,
)
from ai_engine.domain.errors import ErrorCode


class FakeClock:
    """A monotonic clock that only advances when we sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def responder(*statuses: int, headers: dict | None = None):
    """A sender that returns the given statuses in order, then repeats the last."""
    calls: list[int] = []
    sequence = list(statuses)

    async def send() -> httpx.Response:
        status = sequence[min(len(calls), len(sequence) - 1)]
        calls.append(status)
        return httpx.Response(status, headers=headers or {})

    send.calls = calls  # type: ignore[attr-defined]
    return send


def raiser(exc: Exception, *, then: int | None = None):
    """A sender that raises, optionally succeeding with `then` after the first failure."""
    calls: list[str] = []

    async def send() -> httpx.Response:
        calls.append("call")
        if then is not None and len(calls) > 1:
            return httpx.Response(then)
        raise exc

    send.calls = calls  # type: ignore[attr-defined]
    return send


# ── classify(): the decision table, tested directly ─────────────────────────────


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
def test_retryable_statuses_retry(status: int) -> None:
    assert (
        classify(status=status, transport_error=False, can_refresh=False, already_refreshed=False)
        is Disposition.RETRY
    )


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUS - {401}))
def test_terminal_statuses_never_retry(status: int) -> None:
    """A deterministic rejection will be rejected again. Retrying only burns quota."""
    assert (
        classify(status=status, transport_error=False, can_refresh=False, already_refreshed=False)
        is Disposition.TERMINAL
    )


@pytest.mark.parametrize("status", [200, 201, 202, 204, 302, 399])
def test_sub_400_succeeds(status: int) -> None:
    assert (
        classify(status=status, transport_error=False, can_refresh=False, already_refreshed=False)
        is Disposition.SUCCEED
    )


def test_transport_errors_retry() -> None:
    assert (
        classify(status=None, transport_error=True, can_refresh=False, already_refreshed=False)
        is Disposition.RETRY
    )


def test_401_refreshes_once_then_becomes_terminal() -> None:
    """The spec's rule. A second 401 means the credentials are wrong, not stale."""
    first = classify(status=401, transport_error=False, can_refresh=True, already_refreshed=False)
    second = classify(status=401, transport_error=False, can_refresh=True, already_refreshed=True)
    assert first is Disposition.REFRESH_AND_RETRY
    assert second is Disposition.TERMINAL


def test_401_without_a_refresh_hook_is_terminal() -> None:
    """Keycloak's own 401 must never be retried: it means bad credentials."""
    assert (
        classify(status=401, transport_error=False, can_refresh=False, already_refreshed=False)
        is Disposition.TERMINAL
    )


def test_retryable_and_terminal_sets_are_disjoint() -> None:
    assert not (RETRYABLE_STATUS & TERMINAL_STATUS)


# ── Backoff and jitter ──────────────────────────────────────────────────────────


def test_backoff_ceiling_is_exponential_and_capped() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=8.0)
    assert [policy.backoff_ceiling(n) for n in range(1, 7)] == [1, 2, 4, 8, 8, 8]


def test_delay_is_within_full_jitter_bounds() -> None:
    """Full jitter means uniform in [0, ceiling] -- never above, never negative."""
    policy = RetryPolicy(base_delay=1.0, max_delay=30.0)
    rng = random.Random(1234)
    for attempt in range(1, 8):
        ceiling = policy.backoff_ceiling(attempt)
        for _ in range(200):
            delay = policy.delay_for(attempt, rng=rng)
            assert 0.0 <= delay <= ceiling


def test_jitter_actually_varies() -> None:
    """A constant 'jitter' would leave N workers retrying in lockstep."""
    policy = RetryPolicy(base_delay=4.0)
    rng = random.Random(7)
    samples = {round(policy.delay_for(3, rng=rng), 6) for _ in range(50)}
    assert len(samples) > 40, "delays are not being jittered"


def test_jitter_spreads_across_the_window() -> None:
    """Sanity-check the distribution: mean should sit near the middle of [0, ceiling]."""
    policy = RetryPolicy(base_delay=8.0, max_delay=8.0)
    rng = random.Random(99)
    samples = [policy.delay_for(1, rng=rng) for _ in range(2000)]
    assert 3.0 < sum(samples) / len(samples) < 5.0


def test_attempt_is_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        RetryPolicy().backoff_ceiling(0)


def test_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="negative"):
        RetryPolicy(base_delay=-1)


# ── Retry-After ─────────────────────────────────────────────────────────────────


def test_retry_after_delay_seconds_form() -> None:
    response = httpx.Response(429, headers={"Retry-After": "12"})
    assert retry_after_seconds(response) == 12.0


def test_retry_after_zero_is_respected_not_treated_as_absent() -> None:
    response = httpx.Response(429, headers={"Retry-After": "0"})
    assert retry_after_seconds(response) == 0.0


def test_retry_after_http_date_form() -> None:
    """Real gateways emit the date form; ignoring it would waste the server's hint."""
    response = httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:30 GMT"})
    import datetime as _dt

    reference = _dt.datetime(2026, 10, 21, 7, 28, 0, tzinfo=_dt.UTC).timestamp()
    assert retry_after_seconds(response, now=reference) == pytest.approx(30.0, abs=1)


def test_retry_after_in_the_past_clamps_to_zero() -> None:
    response = httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2020 07:28:30 GMT"})
    assert retry_after_seconds(response) == 0.0


def test_retry_after_absent_or_garbage_is_none() -> None:
    assert retry_after_seconds(httpx.Response(429)) is None
    assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "soon"})) is None


# ── send_with_retry(): the loop ─────────────────────────────────────────────────


async def test_success_on_the_first_attempt_does_not_sleep() -> None:
    clock = FakeClock()
    send = responder(200)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(),
        operation="op",
        code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        sleep=clock.sleep,
        clock=clock,
    )
    assert response.status_code == 200
    assert clock.sleeps == []
    assert len(send.calls) == 1


async def test_retries_then_succeeds() -> None:
    clock = FakeClock()
    send = responder(503, 503, 200)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(base_delay=1.0),
        operation="op",
        code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )
    assert response.status_code == 200
    assert len(send.calls) == 3
    assert len(clock.sleeps) == 2, "one sleep between each pair of attempts"


async def test_terminal_status_returns_immediately_without_retrying() -> None:
    clock = FakeClock()
    send = responder(422)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(max_attempts=5),
        operation="op",
        code=ErrorCode.VALIDATION_ERROR,
        sleep=clock.sleep,
        clock=clock,
    )
    assert response.status_code == 422
    assert len(send.calls) == 1
    assert clock.sleeps == []


async def test_attempt_cap_is_honoured_exactly() -> None:
    clock = FakeClock()
    send = responder(503)
    with pytest.raises(RetryExhausted) as caught:
        await send_with_retry(
            send,
            policy=RetryPolicy(max_attempts=3, base_delay=0.1),
            operation="op",
            code=ErrorCode.UPLOAD_FAILED,
            sleep=clock.sleep,
            clock=clock,
            rng=random.Random(0),
        )
    assert len(send.calls) == 3
    assert caught.value.attempts == 3
    assert caught.value.last_status == 503
    assert caught.value.code is ErrorCode.UPLOAD_FAILED
    assert not caught.value.exhausted_budget
    # No sleep after the final attempt -- that would be pure dead time.
    assert len(clock.sleeps) == 2


async def test_total_budget_stops_the_loop_early() -> None:
    """An attempt cap alone lets a slow endpoint hold a worker past any useful deadline."""
    clock = FakeClock()
    send = responder(503)
    with pytest.raises(RetryExhausted) as caught:
        await send_with_retry(
            send,
            policy=RetryPolicy(max_attempts=50, base_delay=10.0, max_delay=10.0, total_budget=15.0),
            operation="op",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            sleep=clock.sleep,
            clock=clock,
            rng=random.Random(0),
        )
    assert caught.value.exhausted_budget
    assert len(send.calls) < 50
    assert clock.now <= 15.0


async def test_transport_error_is_retried_then_recovers() -> None:
    clock = FakeClock()
    send = raiser(httpx.ConnectError("refused"), then=200)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(base_delay=0.1),
        operation="op",
        code=ErrorCode.EXTERNAL_SERVICE_ERROR,
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )
    assert response.status_code == 200
    assert len(send.calls) == 2


async def test_persistent_transport_error_exhausts_with_no_status() -> None:
    clock = FakeClock()
    send = raiser(httpx.ReadTimeout("timed out"))
    with pytest.raises(RetryExhausted) as caught:
        await send_with_retry(
            send,
            policy=RetryPolicy(max_attempts=2, base_delay=0.1),
            operation="op",
            code=ErrorCode.AI_INFERENCE_ERROR,
            sleep=clock.sleep,
            clock=clock,
            rng=random.Random(0),
        )
    assert caught.value.last_status is None
    assert "ReadTimeout" in caught.value.last_error


async def test_401_triggers_one_refresh_and_an_immediate_retry() -> None:
    clock = FakeClock()
    refreshes: list[int] = []

    async def on_unauthorized() -> None:
        refreshes.append(1)

    send = responder(401, 200)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(base_delay=5.0),
        operation="op",
        code=ErrorCode.AUTHENTICATION_ERROR,
        on_unauthorized=on_unauthorized,
        sleep=clock.sleep,
        clock=clock,
    )
    assert response.status_code == 200
    assert len(refreshes) == 1
    # A stale token is not congestion: the retry is immediate, with no backoff.
    assert clock.sleeps == []


async def test_a_second_401_is_terminal_and_does_not_refresh_again() -> None:
    clock = FakeClock()
    refreshes: list[int] = []

    async def on_unauthorized() -> None:
        refreshes.append(1)

    send = responder(401)
    response = await send_with_retry(
        send,
        policy=RetryPolicy(max_attempts=5),
        operation="op",
        code=ErrorCode.AUTHENTICATION_ERROR,
        on_unauthorized=on_unauthorized,
        sleep=clock.sleep,
        clock=clock,
    )
    assert response.status_code == 401
    assert len(refreshes) == 1, "refreshed more than once for a persistent 401"
    assert len(send.calls) == 2


async def test_retry_after_overrides_our_backoff() -> None:
    clock = FakeClock()
    send = responder(429, 200, headers={"Retry-After": "7"})
    await send_with_retry(
        send,
        policy=RetryPolicy(base_delay=100.0, max_delay=1000.0),
        operation="op",
        code=ErrorCode.RATE_LIMITED,
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
    )
    assert clock.sleeps == [7.0], "the server's Retry-After should win over our backoff"


async def test_retry_after_is_capped_by_max_delay() -> None:
    """A hostile or buggy gateway must not be able to park a worker for an hour."""
    clock = FakeClock()
    send = responder(429, 200, headers={"Retry-After": "3600"})
    await send_with_retry(
        send,
        policy=RetryPolicy(max_delay=30.0, total_budget=1000.0),
        operation="op",
        code=ErrorCode.RATE_LIMITED,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.sleeps == [30.0]


async def test_stats_are_reported_on_exhaustion() -> None:
    clock = FakeClock()
    send = responder(503)
    with pytest.raises(RetryExhausted) as caught:
        await send_with_retry(
            send,
            policy=RetryPolicy(max_attempts=3, base_delay=1.0),
            operation="op",
            code=ErrorCode.UPLOAD_FAILED,
            sleep=clock.sleep,
            clock=clock,
            rng=random.Random(3),
        )
    stats = caught.value.stats
    assert stats.attempts == 3
    assert stats.refreshed is False
    assert stats.slept == pytest.approx(sum(clock.sleeps))


async def test_single_attempt_policy_makes_exactly_one_call() -> None:
    clock = FakeClock()
    send = responder(503)
    with pytest.raises(RetryExhausted):
        await send_with_retry(
            send,
            policy=RetryPolicy(max_attempts=1),
            operation="op",
            code=ErrorCode.UPLOAD_FAILED,
            sleep=clock.sleep,
            clock=clock,
        )
    assert len(send.calls) == 1
    assert clock.sleeps == []
