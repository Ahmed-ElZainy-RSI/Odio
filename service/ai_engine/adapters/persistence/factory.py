"""Choosing a job store from configuration.

Its own module because both entrypoints need it: the worker opens the store directly, and
the API opens one inside its lifespan when it was not handed one. Importing this from
`main` would make `api -> main -> api` a cycle.
"""

from __future__ import annotations

from typing import Any

from ai_engine.observability.logging import get_logger
from ai_engine.ports.job_store import JobStore

__all__ = ["open_job_store"]

log = get_logger(__name__)


async def open_job_store(settings: Any) -> JobStore:
    """Build and connect the configured job store.

    `sqlite` is the default because it is restart-safe: a worker that dies no longer takes
    its in-flight jobs with it. `memory` is faster but loses everything on exit, so
    selecting it emits a warning rather than being silently acceptable.

    Must be awaited *inside the loop that will use the store*: the connection is guarded by
    an ``asyncio.Lock``, which binds to the loop that first acquires it.
    """
    kind = getattr(settings, "job_store", "sqlite")

    if kind == "memory":
        from ai_engine.adapters.persistence.memory import InMemoryJobStore

        log.warning(
            "store.not_durable",
            detail=(
                "JOB_STORE=memory keeps job state in this process only; a restart loses "
                "every in-flight job. Use sqlite unless this is a throwaway run."
            ),
        )
        return InMemoryJobStore(lease_seconds=settings.worker_lease_seconds)

    from ai_engine.adapters.persistence.sql import SqliteJobStore

    store = SqliteJobStore(settings.job_store_path, lease_seconds=settings.worker_lease_seconds)
    await store.connect()
    return store
