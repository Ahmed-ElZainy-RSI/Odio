"""The outbox drainer.

Delivers stored callback intents, at-least-once. Retries with backoff, and parks a record
after the attempt cap.

Parking is the one failure this service cannot paper over. Every other error has a way to
tell the Backend something; this one *is* the failure to tell the Backend, so its only
remedy is an alert and a human. ``ai_engine_outbox_pending`` staying above zero means a
book is being stranded right now.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_engine.domain.callbacks import OutboxRecord
from ai_engine.domain.enums import CallbackKind
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import OUTBOX_PENDING, Metrics
from ai_engine.ports.job_store import JobStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DrainResult:
    delivered: int
    failed: int
    parked: int


class OutboxDrainer:
    def __init__(
        self,
        *,
        store: JobStore,
        sender: object,  # BackendJobsClient, or any object with .send(envelope)
        metrics: Metrics,
        max_attempts: int = 10,
    ) -> None:
        self._store = store
        self._sender = sender
        self._metrics = metrics
        self._max_attempts = max_attempts

    async def drain(self, *, limit: int = 100) -> DrainResult:
        records = await self._store.pending_callbacks(limit=limit)
        delivered = failed = parked = 0

        for record in records:
            if record.attempt >= self._max_attempts:
                parked += 1
                self._park(record)
                continue
            try:
                await self._sender.send(record.envelope)  # type: ignore[attr-defined]
            except Exception as exc:
                failed += 1
                await self._store.mark_callback_failed(record.id, f"{type(exc).__name__}")
                log.warning(
                    "outbox.delivery_failed",
                    kind=record.envelope.kind.value,
                    page_number=record.envelope.page_number,
                    attempt=record.attempt + 1,
                    detail=str(exc),
                )
            else:
                delivered += 1
                await self._store.mark_callback_delivered(record.id)
                # metadata delivery is a gate: page work may not start before it lands.
                if record.envelope.kind is CallbackKind.METADATA:
                    await self._store.set_job_state(
                        record.envelope.job_id,
                        (await self._store.get(record.envelope.job_id)).state,  # type: ignore[union-attr]
                        metadata_sent=True,
                    )

        remaining = await self._store.pending_callbacks(limit=10_000)
        self._metrics.set(OUTBOX_PENDING, float(len(remaining)))
        return DrainResult(delivered=delivered, failed=failed, parked=parked)

    def _park(self, record: OutboxRecord) -> None:
        log.error(
            "outbox.parked",
            kind=record.envelope.kind.value,
            page_number=record.envelope.page_number,
            attempts=record.attempt,
            detail=(
                "callback could not be delivered; a book is stranded in the Backend and "
                "a human must intervene"
            ),
        )
