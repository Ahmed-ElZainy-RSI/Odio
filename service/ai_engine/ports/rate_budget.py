"""The rate-budget port.

One method, because there is exactly one question to ask: *may I make this paid call?*
The answer has to be shared across every worker process, which is why this is a port with
a database behind it rather than a counter in memory. See
:mod:`ai_engine.domain.budget` for the policy and the reason it exists.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_engine.domain.budget import BudgetUsage

__all__ = ["RateBudget"]


@runtime_checkable
class RateBudget(Protocol):
    async def acquire(self, provider: str, *, cost: int = 1) -> None:
        """Consume ``cost`` calls of ``provider``'s allowance, or refuse.

        Raises :class:`ai_engine.domain.errors.RateLimitedError` when the window is
        exhausted, carrying ``retry_after_seconds`` so the caller can park the work until
        the allowance returns instead of burning attempts against a wall.

        Providers with no configured limit, and the free local ones, are always allowed.
        The consumption must be atomic across processes: two workers asking at the same
        instant must not both be granted the last call.
        """
        ...

    async def usage(self) -> list[BudgetUsage]:
        """Current window usage for every configured provider, for `/ops` and alerting."""
        ...
