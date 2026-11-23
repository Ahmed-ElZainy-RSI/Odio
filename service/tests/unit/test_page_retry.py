"""Spec 036: the rule that decides whether a failed page attempt is tried again.

``next_page_attempt`` answers one question after an attempt fails: wait this many seconds and
run the page again, or report it failed now (``None``). ``attempts_made`` counts the attempt
that just failed, so it is always at least 1.
"""

from __future__ import annotations

import pytest

from ai_engine.domain.errors import ErrorCode, FailureReason
from ai_engine.domain.page_retry import next_page_attempt, page_attempt_limit

# Retryable codes that a page task can end with and that get the full attempt allowance.
# RATE_LIMITED (parking owns it), PROCESSING_TIMEOUT (retried once) and INTERNAL_ERROR (a bug)
# have their own tests below.
FULLY_RETRIED = [
    ErrorCode.AI_INFERENCE_ERROR,
    ErrorCode.MODEL_UNAVAILABLE,
    ErrorCode.UPLOAD_FAILED,
    ErrorCode.SOURCE_UNAVAILABLE,
    ErrorCode.EXTERNAL_SERVICE_ERROR,
    ErrorCode.CALLBACK_FAILED,
]


def reason(code: ErrorCode) -> FailureReason:
    return FailureReason(code, "provider said no")


def always(value: float):
    return lambda: value


# ── Retry or report ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", FULLY_RETRIED)
def test_a_retryable_failure_with_attempts_left_is_tried_again(code: ErrorCode) -> None:
    for attempts_made in (1, 2):
        delay = next_page_attempt(reason(code), attempts_made, 3, rng=always(0.5))
        assert delay is not None and delay > 0


@pytest.mark.parametrize("code", FULLY_RETRIED)
def test_a_retryable_failure_with_no_attempts_left_is_reported(code: ErrorCode) -> None:
    assert next_page_attempt(reason(code), 3, 3) is None
    assert next_page_attempt(reason(code), 4, 3) is None, "past the cap is still the cap"


@pytest.mark.parametrize("code", [c for c in ErrorCode if not c.retryable])
def test_a_non_retryable_failure_is_reported_on_the_first_attempt(code: ErrorCode) -> None:
    assert next_page_attempt(reason(code), 1, 3) is None


def test_an_internal_error_is_never_retried() -> None:
    """A bug fails the same way every time; retrying it only repeats paid calls (FR-003)."""
    assert next_page_attempt(reason(ErrorCode.INTERNAL_ERROR), 1, 3) is None


def test_a_quota_wall_is_left_to_parking() -> None:
    """RATE_LIMITED parks the page without counting an attempt; this rule never sees it as
    a retry (FR-001)."""
    assert next_page_attempt(reason(ErrorCode.RATE_LIMITED), 1, 3) is None


def test_a_page_timeout_is_retried_once() -> None:
    timeout = reason(ErrorCode.PROCESSING_TIMEOUT)
    assert next_page_attempt(timeout, 1, 3, rng=always(0.5)) is not None
    assert next_page_attempt(timeout, 2, 3) is None


@pytest.mark.parametrize("code", list(ErrorCode))
def test_one_allowed_attempt_turns_retries_off(code: ErrorCode) -> None:
    """PAGE_MAX_ATTEMPTS=1 is exactly today's behaviour: first failure is the report."""
    assert next_page_attempt(reason(code), 1, 1) is None


# ── Attempt limits ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", FULLY_RETRIED)
def test_the_limit_is_the_configured_maximum(code: ErrorCode) -> None:
    assert page_attempt_limit(code, 3) == 3
    assert page_attempt_limit(code, 5) == 5


def test_the_timeout_limit_never_exceeds_the_configured_maximum() -> None:
    assert page_attempt_limit(ErrorCode.PROCESSING_TIMEOUT, 3) == 2
    assert page_attempt_limit(ErrorCode.PROCESSING_TIMEOUT, 1) == 1


@pytest.mark.parametrize(("attempts_made", "max_attempts"), [(0, 3), (-1, 3), (1, 0)])
def test_nonsense_counts_are_refused(attempts_made: int, max_attempts: int) -> None:
    with pytest.raises(ValueError):
        next_page_attempt(reason(ErrorCode.AI_INFERENCE_ERROR), attempts_made, max_attempts)


# ── Backoff: exponential, full jitter, capped (FR-002) ──────────────────────────────────


@pytest.mark.parametrize(
    ("attempts_made", "ceiling"),
    [(1, 5.0), (2, 10.0), (3, 20.0), (4, 40.0), (5, 80.0), (6, 120.0), (9, 120.0)],
)
def test_backoff_doubles_from_the_base_up_to_the_cap(attempts_made: int, ceiling: float) -> None:
    delay = next_page_attempt(
        reason(ErrorCode.AI_INFERENCE_ERROR),
        attempts_made,
        10,
        base_seconds=5.0,
        cap_seconds=120.0,
        rng=always(1.0),
    )
    assert delay == pytest.approx(ceiling)


def test_backoff_is_full_jitter_over_the_ceiling() -> None:
    delay = next_page_attempt(
        reason(ErrorCode.AI_INFERENCE_ERROR),
        2,
        3,
        base_seconds=5.0,
        cap_seconds=120.0,
        rng=always(0.25),
    )
    assert delay == pytest.approx(0.25 * 10.0)


@pytest.mark.parametrize("draw", [0.0, 0.5, 0.999999])
def test_backoff_is_never_negative_and_never_above_the_cap(draw: float) -> None:
    for attempts_made in range(1, 12):
        delay = next_page_attempt(
            reason(ErrorCode.AI_INFERENCE_ERROR),
            attempts_made,
            12,
            base_seconds=5.0,
            cap_seconds=120.0,
            rng=always(draw),
        )
        assert delay is not None
        assert 0.0 <= delay <= 120.0


def test_the_defaults_are_the_documented_base_and_cap() -> None:
    """09-async-jobs.md:286 — base 5 s, cap 120 s."""
    first = next_page_attempt(reason(ErrorCode.AI_INFERENCE_ERROR), 1, 10, rng=always(1.0))
    late = next_page_attempt(reason(ErrorCode.AI_INFERENCE_ERROR), 9, 10, rng=always(1.0))
    assert first == pytest.approx(5.0)
    assert late == pytest.approx(120.0)


def test_the_default_jitter_varies_and_stays_in_range() -> None:
    delays = {next_page_attempt(reason(ErrorCode.AI_INFERENCE_ERROR), 3, 5) for _ in range(50)}
    assert len(delays) > 1, "jitter must actually spread retries out"
    assert all(d is not None and 0.0 <= d <= 20.0 for d in delays)
