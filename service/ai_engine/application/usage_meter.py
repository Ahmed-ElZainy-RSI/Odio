"""Persist one stage's paid-call usage and count its cost (spec 025).

Used by both job runners after a stage that may call a paid provider. Saving usage must
never fail a page -- the page's work is done and billed either way -- so a sink error is
logged and swallowed here, deliberately, and only here.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_engine.domain.errors import PROGRAMMING_ERRORS
from ai_engine.domain.job import Job
from ai_engine.domain.usage import PriceTable, UsageRecord, cost_of
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import COST_USD, Metrics
from ai_engine.ports.usage import UsageSink

log = get_logger(__name__)


class UsageMeter:
    def __init__(self, *, sink: UsageSink, prices: PriceTable, metrics: Metrics) -> None:
        self._sink = sink
        self._prices = prices
        self._metrics = metrics

    async def save(self, job: Job, records: Sequence[UsageRecord]) -> None:
        if not records:
            return
        tier = job.quality_tier.value
        for record in records:
            usd = cost_of(record, self._prices)
            if usd:
                self._metrics.inc(
                    COST_USD,
                    {"provider": record.provider, "model": record.model, "tier": tier},
                    amount=usd,
                )
        try:
            await self._sink.write(records, job_id=str(job.job_id), tier=tier)
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            log.warning("usage.save_failed", records=len(records), error=type(exc).__name__)
