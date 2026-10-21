"""Entrypoints.

One image, three roles, one command each: `ai-engine-api`, `ai-engine-worker`,
`ai-engine-reconciler`. Every role must be pointed at the same `JOB_STORE_PATH` -- the API
writes the job, the worker claims it, the reconciler enforces its deadline, and they
coordinate through nothing else.
"""

from __future__ import annotations

import os
import socket
import sys

from ai_engine.adapters.persistence.factory import open_job_store
from ai_engine.config.settings import Role, assert_no_dotenv_on_disk, get_settings


def run_api() -> None:
    """Serve the HTTP API."""
    import uvicorn

    assert_no_dotenv_on_disk()
    settings = get_settings()

    from ai_engine.api.app import create_app

    uvicorn.run(
        create_app(settings=settings),
        host="0.0.0.0",
        port=settings.ai_engine_port,
        log_config=None,  # structlog owns stdout
    )


def run_worker() -> None:  # pragma: no cover - exercised by tests at the loop level
    """Claim and run jobs until asked to stop."""
    import asyncio

    assert_no_dotenv_on_disk()
    settings = get_settings()
    settings.require_secrets()

    async def main_loop() -> None:
        import httpx

        from ai_engine.observability.logging import configure_logging, get_logger
        from ai_engine.observability.metrics import METRICS
        from ai_engine.worker import build_worker

        configure_logging(
            level=settings.ai_engine_log_level,
            json_logs=settings.ai_engine_json_logs,
            role="worker",
            version=settings.ai_engine_version,
        )
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout_seconds,
            read=settings.http_read_timeout_seconds,
            write=settings.http_read_timeout_seconds,
            pool=settings.http_read_timeout_seconds,
        )
        log = get_logger("ai_engine.main")
        store = await open_job_store(settings)
        async with httpx.AsyncClient(timeout=timeout) as client:
            context = build_worker(
                settings=settings, store=store, metrics=METRICS, http_client=client
            )
            loop = context.loop(
                # Distinct per replica: this lands in the store's `claimed_by`, and three
                # workers all called "worker-worker" make a lease impossible to attribute.
                # In Docker and Kubernetes the hostname is the container/pod name.
                worker_name=f"{socket.gethostname()}-{os.getpid()}",
                reconcile_every_ticks=max(1, settings.reconcile_interval_seconds),
            )
            loop.install_signal_handlers()
            try:
                await loop.run()
            finally:
                for owned in (store, context.budget, context.usage):
                    closer = getattr(owned, "close", None)
                    if closer is not None:
                        await closer()
                log.info("worker.exited")

    asyncio.run(main_loop())


def run_reconciler() -> None:  # pragma: no cover - exercised by tests at the loop level
    """Run only the deadline sweep and the outbox drain, on a timer.

    Deployable as its own container, or left off entirely -- the worker loop already
    reconciles periodically. It claims no jobs, so it never competes with a worker.

    Run **exactly one**. The reconciler only moves non-terminal pages to terminal, which is
    idempotent in the store, but two of them sweeping the same abandoned job would each
    form a terminal intent for its pages and the outbox would have to reject one.
    """
    import asyncio

    assert_no_dotenv_on_disk()
    settings = get_settings()
    settings.require_secrets()

    if settings.job_store == "memory":
        raise SystemExit(
            "the reconciler role needs a store it can share with the workers; "
            "JOB_STORE=memory is per-process, so it would sweep an empty database"
        )

    async def main_loop() -> None:
        import httpx

        from ai_engine.observability.logging import configure_logging, get_logger
        from ai_engine.observability.metrics import METRICS
        from ai_engine.worker import build_worker

        configure_logging(
            level=settings.ai_engine_log_level,
            json_logs=settings.ai_engine_json_logs,
            role="reconciler",
            version=settings.ai_engine_version,
        )
        log = get_logger("ai_engine.main")
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout_seconds,
            read=settings.http_read_timeout_seconds,
            write=settings.http_read_timeout_seconds,
            pool=settings.http_read_timeout_seconds,
        )
        store = await open_job_store(settings)
        async with httpx.AsyncClient(timeout=timeout) as client:
            context = build_worker(
                settings=settings, store=store, metrics=METRICS, http_client=client
            )
            loop = context.reconcile_loop(
                worker_name=f"reconciler-{socket.gethostname()}",
                # One tick per interval: the tick's idle sleep *is* the schedule.
                idle_sleep_seconds=float(settings.reconcile_interval_seconds),
            )
            loop.install_signal_handlers()
            try:
                await loop.run()
            finally:
                for owned in (store, context.budget, context.usage):
                    closer = getattr(owned, "close", None)
                    if closer is not None:
                        await closer()
                log.info("reconciler.exited")

    asyncio.run(main_loop())


def main() -> None:  # pragma: no cover - thin dispatch
    settings = get_settings()
    match settings.ai_engine_role:
        case Role.API:
            run_api()
        case Role.WORKER:
            run_worker()
        case Role.RECONCILER:
            run_reconciler()
        case other:
            sys.exit(f"unknown AI_ENGINE_ROLE: {other}")


if __name__ == "__main__":  # pragma: no cover
    main()
