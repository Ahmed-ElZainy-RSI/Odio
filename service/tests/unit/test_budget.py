"""Provider budgets: the policy, the counters, and the fleet-wide guarantee.

`T-5.7`. The behaviour under test is the one that actually bit this project: a free tier
of 10 calls a day, a book of 31 pages, and no coordination between workers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ai_engine.adapters.persistence import rate_budget as budget_module
from ai_engine.adapters.persistence.rate_budget import (
    InMemoryBudget,
    SqlBudget,
    UnlimitedBudget,
    build_rate_budget,
)
from ai_engine.config.settings import Settings
from ai_engine.domain.budget import (
    ProviderLimit,
    decide,
    parse_limit,
    parse_limits,
)
from ai_engine.domain.errors import ErrorCode, RateLimitedError
from ai_engine.observability.metrics import BUDGET_EXHAUSTED, Metrics
from ai_engine.ports.rate_budget import RateBudget

DAY = 86_400
GEMINI_FREE_TIER = ProviderLimit(provider="gemini", max_calls=10, period_seconds=DAY)


# ── The policy (pure) ───────────────────────────────────────────────────────────


def test_a_spec_parses_into_a_limit() -> None:
    assert parse_limit("gemini=10/86400") == GEMINI_FREE_TIER


@pytest.mark.parametrize(
    "spec",
    [" gemini = 10 / 86400 ", "GEMINI=10/86400", "gemini=10/86400\n"],
)
def test_specs_are_forgiving_about_whitespace_and_case(spec: str) -> None:
    """These arrive from environment variables typed by hand."""
    assert parse_limit(spec) == GEMINI_FREE_TIER


@pytest.mark.parametrize(
    "spec",
    [
        "gemini",  # no allowance
        "gemini=10",  # no period
        "=10/86400",  # no provider
        "gemini=ten/86400",  # not a number
        "gemini=10/day",
        "gemini=10/0",  # zero-length window
        "gemini=-1/86400",  # negative allowance
    ],
)
def test_a_malformed_spec_is_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_limit(spec)


def test_a_zero_budget_is_legal_and_blocks_everything() -> None:
    """`provider=0/86400` is the kill switch for a provider. Not an error."""
    limit = parse_limit("lighton=0/86400")
    assert limit.max_calls == 0
    assert not decide(limit, used=0, now=datetime.now(tz=UTC)).allowed


def test_later_specs_win() -> None:
    limits = parse_limits(["gemini=10/86400", "gemini=1000/86400"])
    assert limits["gemini"].max_calls == 1000


def test_blank_specs_are_ignored() -> None:
    assert parse_limits(["", "  ", "gemini=10/86400"]) == {"gemini": GEMINI_FREE_TIER}


def test_windows_are_epoch_aligned_so_every_process_agrees() -> None:
    """Two workers must derive the same window from a timestamp, with no coordination."""
    limit = ProviderLimit(provider="p", max_calls=1, period_seconds=DAY)
    morning = datetime(2026, 9, 7, 3, 14, 15, tzinfo=UTC)
    evening = datetime(2026, 9, 7, 23, 59, 59, tzinfo=UTC)
    assert limit.window_start(morning) == datetime(2026, 9, 7, tzinfo=UTC)
    assert limit.window_start(evening) == datetime(2026, 9, 7, tzinfo=UTC)
    assert limit.window_reset(morning) == datetime(2026, 9, 8, tzinfo=UTC)


def test_the_window_rolls_over_at_the_boundary() -> None:
    limit = ProviderLimit(provider="p", max_calls=1, period_seconds=DAY)
    just_after = datetime(2026, 9, 8, 0, 0, 1, tzinfo=UTC)
    assert limit.window_start(just_after) == datetime(2026, 9, 8, tzinfo=UTC)


def test_retry_after_is_the_time_until_the_allowance_returns() -> None:
    now = datetime(2026, 9, 7, 23, 0, 0, tzinfo=UTC)
    decision = decide(GEMINI_FREE_TIER, used=10, now=now)
    assert not decision.allowed
    assert decision.retry_after_seconds == 3600.0
    assert decision.remaining == 0


def test_retry_after_is_zero_when_the_call_is_allowed() -> None:
    decision = decide(GEMINI_FREE_TIER, used=0, now=datetime.now(tz=UTC))
    assert decision.allowed
    assert decision.retry_after_seconds == 0.0
    assert decision.remaining == 10


def test_a_multi_call_cost_must_fit_entirely() -> None:
    """Nine used, one left, a two-call request: refused. No partial spends."""
    decision = decide(GEMINI_FREE_TIER, used=9, cost=2, now=datetime.now(tz=UTC))
    assert not decision.allowed


def test_cost_must_be_positive() -> None:
    with pytest.raises(ValueError, match="cost"):
        decide(GEMINI_FREE_TIER, used=0, cost=0, now=datetime.now(tz=UTC))


@given(
    max_calls=st.integers(min_value=0, max_value=50),
    used=st.integers(min_value=0, max_value=100),
)
def test_a_decision_never_lets_usage_exceed_the_ceiling(max_calls: int, used: int) -> None:
    """The property the whole feature exists for."""
    limit = ProviderLimit(provider="p", max_calls=max_calls, period_seconds=DAY)
    decision = decide(limit, used=used, now=datetime.now(tz=UTC))
    if decision.allowed:
        assert used + 1 <= max_calls
    else:
        assert decision.retry_after_seconds > 0 or max_calls == 0


# ── The counters ────────────────────────────────────────────────────────────────


@pytest.fixture(params=["memory", "sql"])
def budget_factory(request: pytest.FixtureRequest, tmp_path: Path):
    """Both counter implementations, tested against the same contract."""

    def build(limits: dict[str, ProviderLimit], *, metrics: Metrics | None = None) -> RateBudget:
        if request.param == "memory":
            return InMemoryBudget(limits, metrics=metrics)
        return SqlBudget(tmp_path / "jobs.db", limits, metrics=metrics)

    build.kind = request.param  # type: ignore[attr-defined]
    return build


async def test_both_counters_satisfy_the_port(budget_factory) -> None:
    assert isinstance(budget_factory({}), RateBudget)


async def test_the_allowance_is_spent_exactly_once_per_call(budget_factory) -> None:
    budget = budget_factory({"gemini": ProviderLimit("gemini", 3, DAY)})
    for _ in range(3):
        await budget.acquire("gemini")
    with pytest.raises(RateLimitedError):
        await budget.acquire("gemini")


async def test_exhaustion_carries_a_retryable_code_and_a_retry_after(budget_factory) -> None:
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, DAY)})
    await budget.acquire("gemini")
    with pytest.raises(RateLimitedError) as caught:
        await budget.acquire("gemini")
    assert caught.value.reason.code is ErrorCode.RATE_LIMITED
    assert caught.value.retryable, "a quota comes back; this is not permanent"
    assert 0 < caught.value.retry_after_seconds <= DAY


async def test_the_refusal_message_says_which_provider_and_how_much(budget_factory) -> None:
    """An operator reading one log line should not have to guess."""
    budget = budget_factory({"lighton": ProviderLimit("lighton", 2, DAY)})
    await budget.acquire("lighton")
    await budget.acquire("lighton")
    with pytest.raises(RateLimitedError, match=r"lighton budget exhausted: 2/2"):
        await budget.acquire("lighton")


async def test_providers_have_separate_allowances(budget_factory) -> None:
    budget = budget_factory(
        {
            "gemini": ProviderLimit("gemini", 1, DAY),
            "lighton": ProviderLimit("lighton", 1, DAY),
        }
    )
    await budget.acquire("gemini")
    await budget.acquire("lighton")  # unaffected by gemini's exhaustion
    with pytest.raises(RateLimitedError):
        await budget.acquire("gemini")


async def test_an_unbudgeted_provider_is_never_refused(budget_factory) -> None:
    """Absence of a limit means "not metered", never "zero allowed"."""
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, DAY)})
    for _ in range(50):
        await budget.acquire("lighton")


async def test_the_free_local_engine_is_never_metered(budget_factory) -> None:
    budget = budget_factory({"local": ProviderLimit("local", 1, DAY)})
    for _ in range(50):
        await budget.acquire("local")


async def test_a_provider_name_is_matched_case_insensitively(budget_factory) -> None:
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, DAY)})
    await budget.acquire("Gemini")
    with pytest.raises(RateLimitedError):
        await budget.acquire("GEMINI")


async def test_a_refused_call_does_not_consume_anything(budget_factory) -> None:
    """Otherwise a refused page would eat tomorrow's allowance too."""
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, DAY)})
    await budget.acquire("gemini")
    for _ in range(5):
        with pytest.raises(RateLimitedError):
            await budget.acquire("gemini")
    (usage,) = await budget.usage()
    assert usage.used == 1


async def test_a_multi_call_charge_is_all_or_nothing(budget_factory) -> None:
    budget = budget_factory({"gemini": ProviderLimit("gemini", 3, DAY)})
    await budget.acquire("gemini", cost=2)
    with pytest.raises(RateLimitedError):
        await budget.acquire("gemini", cost=2)
    (usage,) = await budget.usage()
    assert usage.used == 2, "the refused charge must not have been partially applied"
    await budget.acquire("gemini")  # the remaining single call still fits


async def test_usage_reports_the_current_window(budget_factory) -> None:
    budget = budget_factory({"gemini": ProviderLimit("gemini", 10, DAY)})
    await budget.acquire("gemini", cost=4)
    (usage,) = await budget.usage()
    assert (usage.provider, usage.used, usage.remaining, usage.max_calls) == ("gemini", 4, 6, 10)
    assert usage.window_reset - usage.window_start == timedelta(seconds=DAY)


async def test_usage_lists_every_configured_provider_even_at_zero(budget_factory) -> None:
    budget = budget_factory(
        {
            "gemini": ProviderLimit("gemini", 10, DAY),
            "lighton": ProviderLimit("lighton", 5, DAY),
        }
    )
    assert {entry.provider for entry in await budget.usage()} == {"gemini", "lighton"}


async def test_the_allowance_returns_when_the_window_rolls_over(
    budget_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of parking rather than failing: tomorrow it works."""
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, 60)})
    start = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(budget_module, "_now", lambda: start)
    await budget.acquire("gemini")
    with pytest.raises(RateLimitedError):
        await budget.acquire("gemini")

    monkeypatch.setattr(budget_module, "_now", lambda: start + timedelta(seconds=61))
    await budget.acquire("gemini")


async def test_exhaustion_is_counted_as_a_metric(budget_factory) -> None:
    """`remaining == 0` has to be alertable: nothing else looks broken."""
    metrics = Metrics()
    budget = budget_factory({"gemini": ProviderLimit("gemini", 1, DAY)}, metrics=metrics)
    await budget.acquire("gemini")
    with pytest.raises(RateLimitedError):
        await budget.acquire("gemini")
    assert metrics.counter_value(BUDGET_EXHAUSTED, {"provider": "gemini"}) == 1


async def test_concurrent_callers_cannot_oversubscribe(budget_factory) -> None:
    """Ten allowed, forty asking, all at once. Exactly ten may win."""
    budget = budget_factory({"gemini": ProviderLimit("gemini", 10, DAY)})

    async def attempt() -> bool:
        try:
            await budget.acquire("gemini")
            return True
        except RateLimitedError:
            return False

    results = await asyncio.gather(*(attempt() for _ in range(40)))
    assert sum(results) == 10
    (usage,) = await budget.usage()
    assert usage.used == 10


# ── Fleet-wide: the property a per-process counter cannot have ──────────────────


async def test_provider_budget_enforced_fleet_wide(tmp_path: Path) -> None:
    """Two workers, one allowance.

    This is the test the roadmap names for `T-5.7`. Two `SqlBudget` instances with
    separate connections stand in for two worker processes; a per-process counter would
    let each of them spend the full ten.
    """
    limits = {"gemini": ProviderLimit("gemini", 10, DAY)}
    worker_a = SqlBudget(tmp_path / "jobs.db", limits)
    worker_b = SqlBudget(tmp_path / "jobs.db", limits)

    for _ in range(5):
        await worker_a.acquire("gemini")
    for _ in range(5):
        await worker_b.acquire("gemini")

    for worker in (worker_a, worker_b):
        with pytest.raises(RateLimitedError):
            await worker.acquire("gemini")

    (usage,) = await worker_a.usage()
    assert usage.used == 10
    await worker_a.close()
    await worker_b.close()


async def test_a_restart_does_not_refill_the_allowance(tmp_path: Path) -> None:
    """A crash-loop must not become a way to make unlimited paid calls."""
    limits = {"gemini": ProviderLimit("gemini", 2, DAY)}
    async with SqlBudget(tmp_path / "jobs.db", limits) as before:
        await before.acquire("gemini")
        await before.acquire("gemini")

    async with SqlBudget(tmp_path / "jobs.db", limits) as after:
        with pytest.raises(RateLimitedError):
            await after.acquire("gemini")


async def test_the_budget_shares_the_job_store_database(tmp_path: Path) -> None:
    """One file, one schema. A second database would be a second thing to back up."""
    from ai_engine.adapters.persistence.sql import SqliteJobStore

    path = tmp_path / "jobs.db"
    async with SqliteJobStore(path) as store:
        assert await store.get(__import__("uuid").UUID(int=0)) is None
        async with SqlBudget(path, {"gemini": ProviderLimit("gemini", 1, DAY)}) as budget:
            await budget.acquire("gemini")
            (usage,) = await budget.usage()
            assert usage.used == 1


async def test_old_windows_are_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A daily counter left running for a year should not leave 365 rows per provider."""
    limits = {"gemini": ProviderLimit("gemini", 100, 60)}
    async with SqlBudget(tmp_path / "jobs.db", limits) as budget:
        start = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
        for minute in range(10):
            monkeypatch.setattr(
                budget_module, "_now", lambda m=minute: start + timedelta(seconds=60 * m)
            )
            await budget.acquire("gemini")

        def count(connection) -> int:
            return connection.execute("SELECT COUNT(*) AS n FROM provider_budget").fetchone()["n"]

        assert await budget._run(count) <= 4, "only the recent windows are kept"


# ── Selection ───────────────────────────────────────────────────────────────────


def test_no_configured_budgets_means_nothing_is_metered() -> None:
    """The default. A wrong low default would silently park real books."""
    budget = build_rate_budget(settings=Settings())
    assert isinstance(budget, UnlimitedBudget)


def test_a_sql_store_gets_the_fleet_wide_budget(tmp_path: Path) -> None:
    budget = build_rate_budget(
        settings=Settings(
            job_store="sqlite",
            job_store_path=tmp_path / "jobs.db",
            provider_call_budgets=["gemini=10/86400"],
        )
    )
    assert isinstance(budget, SqlBudget)
    assert budget.fleet_wide


def test_the_memory_store_can_only_have_a_per_process_budget() -> None:
    """It is not fleet-wide, and it does not pretend to be."""
    budget = build_rate_budget(
        settings=Settings(job_store="memory", provider_call_budgets=["gemini=10/86400"])
    )
    assert isinstance(budget, InMemoryBudget)
    assert not budget.fleet_wide


def test_a_malformed_budget_spec_fails_at_boot() -> None:
    with pytest.raises(ValueError, match="calls/seconds"):
        Settings(provider_call_budgets=["gemini=10"])
