"""Provider call budgets -- pure policy, no I/O.

Why this exists at all: the free tiers this system runs on are *small*. `README.md:491`
records a real ceiling of **10 requests/day per model** on the Gemini free tier, and a Hume
quota that ran out mid-book and left `work/المومياوات ناشيونال جيوجرافيك` with 33 page
records and zero audio. A book is 31.4 pages on average, so a single book can exhaust a
day's allowance before it finishes -- and the pages that follow do not merely fail, they
fail *identically*, burning attempts and stalling the job in the Backend.

The counting and the decision live here so they can be tested without a database; the
shared counter itself is infrastructure (`adapters/persistence/rate_budget.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = [
    "BudgetDecision",
    "BudgetUsage",
    "ProviderLimit",
    "decide",
    "parse_limit",
    "parse_limits",
]

#: Providers that are never metered: they cost nothing and run locally.
FREE_PROVIDERS = frozenset({"local", "none", ""})


@dataclass(frozen=True, slots=True)
class ProviderLimit:
    """At most ``max_calls`` calls to ``provider`` per fixed ``period_seconds`` window."""

    provider: str
    max_calls: int
    period_seconds: int

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider must not be empty")
        if self.max_calls < 0:
            raise ValueError(f"max_calls must not be negative, got {self.max_calls}")
        if self.period_seconds <= 0:
            raise ValueError(f"period_seconds must be positive, got {self.period_seconds}")

    def window_start(self, now: datetime) -> datetime:
        """Start of the window containing ``now``.

        Windows are *fixed* and epoch-aligned, not sliding. Fixed windows are the only kind
        that two processes can agree on from a timestamp alone, with no coordination -- and
        agreement is the whole point of a fleet-wide budget. The cost is the usual
        boundary burst (up to ``2 * max_calls`` across a boundary), which is acceptable
        here: the ceiling exists to avoid a wall, not to smooth traffic.
        """
        epoch_seconds = int(now.timestamp())
        floored = epoch_seconds - (epoch_seconds % self.period_seconds)
        return datetime.fromtimestamp(floored, tz=UTC)

    def window_reset(self, now: datetime) -> datetime:
        """When the window containing ``now`` rolls over and the allowance returns."""
        return self.window_start(now) + timedelta(seconds=self.period_seconds)


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    used: int
    max_calls: int
    retry_after_seconds: float

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """What ops needs to see on `/ops/budget`."""

    provider: str
    used: int
    max_calls: int
    window_start: datetime
    window_reset: datetime

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)


def decide(limit: ProviderLimit, *, used: int, cost: int = 1, now: datetime) -> BudgetDecision:
    """Would ``cost`` more calls fit inside ``limit``'s current window?

    ``used`` is the count already recorded for that window. ``retry_after_seconds`` is the
    time until the allowance returns, so a caller can park work instead of retrying into a
    wall -- it is only meaningful when the decision is a refusal.
    """
    if cost < 1:
        raise ValueError(f"cost must be at least 1, got {cost}")
    allowed = used + cost <= limit.max_calls
    retry_after = 0.0 if allowed else max(0.0, (limit.window_reset(now) - now).total_seconds())
    return BudgetDecision(
        allowed=allowed,
        used=used,
        max_calls=limit.max_calls,
        retry_after_seconds=retry_after,
    )


def parse_limit(spec: str) -> ProviderLimit:
    """Parse one ``provider=calls/seconds`` spec, e.g. ``gemini=10/86400``.

    A hand-editable string rather than a nested structure, because this is set as an
    environment variable in every deployment target we have.
    """
    text = spec.strip()
    if "=" not in text:
        raise ValueError(f"expected 'provider=calls/seconds', got {spec!r}")
    provider, _, allowance = text.partition("=")
    provider = provider.strip().lower()
    if "/" not in allowance:
        raise ValueError(f"expected 'calls/seconds' after '=', got {allowance!r}")
    calls_text, _, period_text = allowance.partition("/")
    try:
        max_calls = int(calls_text.strip())
        period_seconds = int(period_text.strip())
    except ValueError as exc:
        raise ValueError(f"calls and seconds must be integers in {spec!r}") from exc
    return ProviderLimit(provider=provider, max_calls=max_calls, period_seconds=period_seconds)


def parse_limits(specs: list[str] | tuple[str, ...]) -> dict[str, ProviderLimit]:
    """Parse many specs into a provider-keyed map. A later spec wins over an earlier one."""
    limits: dict[str, ProviderLimit] = {}
    for spec in specs:
        if not spec.strip():
            continue
        limit = parse_limit(spec)
        limits[limit.provider] = limit
    return limits
