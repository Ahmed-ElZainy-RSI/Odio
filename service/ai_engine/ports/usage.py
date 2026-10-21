"""Where paid-call usage is kept (spec 025)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_engine.domain.usage import UsageRecord


@runtime_checkable
class UsageSink(Protocol):
    """Append-only usage rows, readable per job.

    ``job_id`` and ``tier`` travel beside the record rather than inside it: they are
    service concepts the shared (CLI + service) UsageRecord does not have.
    """

    async def write(
        self, records: Sequence[UsageRecord], *, job_id: str | None, tier: str | None
    ) -> None: ...

    async def records(self, *, job_id: str | None = None) -> list[UsageRecord]: ...
