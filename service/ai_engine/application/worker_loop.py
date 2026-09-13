"""The worker runtime loop.

Claims jobs, runs them, drains the callback outbox, and reconciles on a timer. Runs until
asked to stop.

The correctness property worth stating explicitly: **a hard kill must remain safe.** Leases
and the reconciler are what guarantee that, so graceful shutdown here is an optimisation
that avoids re-doing paid work -- not a correctness mechanism. If this loop is SIGKILLed
mid-page, the lease expires, the page is re-claimed, and the reconciler is the backstop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ai_engine.domain.enums import JobState
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import QUEUE_DEPTH, Metrics
from ai_engine.ports.job_store import JobStore

log = get_logger(__name__)


@dataclass
class LoopStats:
    """What the loop did. Returned by :meth:`WorkerLoop.run` and used by tests."""

    ticks: int = 0
    jobs_run: int = 0
    jobs_failed: int = 0
    callbacks_delivered: int = 0
    reconcile_runs: int = 0
    errors: list[str] = field(default_factory=list)


class WorkerLoop:
    """Polls for work until stopped."""

    def __init__(
        self,
        *,
        store: JobStore,
        run_job: Callable[[object], Awaitable[object]],
        drain: Callable[[], Awaitable[object]],
        reconcile: Callable[[], Awaitable[object]] | None = None,
        metrics: Metrics,
        worker_name: str = "worker-1",
        idle_sleep_seconds: float = 1.0,
        reconcile_every_ticks: int = 60,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        claim_jobs: bool = True,
    ) -> None:
        self._store = store
        self._run_job = run_job
        self._drain = drain
        self._reconcile = reconcile
        self._metrics = metrics
        self._worker = worker_name
        self._idle_sleep = idle_sleep_seconds
        self._reconcile_every = reconcile_every_ticks
        self._sleep = sleep
        # The reconciler role runs this same loop with claiming off: it must sweep
        # deadlines and drain the outbox WITHOUT taking jobs away from the workers.
        self._claim_jobs = claim_jobs
        self._stopping = asyncio.Event()
        self._draining = False

    # ── Control ───────────────────────────────────────────────────────────────────

    def request_stop(self) -> None:
        """Ask the loop to finish its current job and exit. Idempotent."""
        if not self._stopping.is_set():
            log.info("worker.stop_requested", worker=self._worker)
            self._stopping.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def install_signal_handlers(self) -> None:
        """Wire SIGTERM/SIGINT to a graceful stop, when a loop is running."""
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - platform
                log.warning("worker.signal_handler_unavailable", signal=sig.name)

    # ── The loop ──────────────────────────────────────────────────────────────────

    async def run(self, *, max_ticks: int | None = None) -> LoopStats:
        """Run until stopped, or for at most ``max_ticks`` iterations.

        ``max_ticks`` exists so tests can drive the loop deterministically instead of
        racing a background task.
        """
        stats = LoopStats()
        log.info("worker.started", worker=self._worker)

        while not self._stopping.is_set():
            if max_ticks is not None and stats.ticks >= max_ticks:
                break
            stats.ticks += 1

            claimed = None
            try:
                claimed = await self._store.claim_job(self._worker) if self._claim_jobs else None
            except Exception as exc:
                # A store outage must not kill the worker: it backs off and retries, which
                # is the same posture the API takes when it cannot persist.
                stats.errors.append(f"claim: {type(exc).__name__}")
                log.error("worker.claim_failed", detail=f"{type(exc).__name__}")
                await self._sleep(self._idle_sleep)
                continue

            if claimed is not None:
                try:
                    outcome = await self._run_job(claimed)
                    stats.jobs_run += 1
                    state = getattr(outcome, "state", None)
                    if state is JobState.FAILED:
                        stats.jobs_failed += 1
                except Exception as exc:
                    # The job runner already converts errors into callbacks; reaching here
                    # means something escaped it, which is a bug worth shouting about.
                    stats.jobs_failed += 1
                    stats.errors.append(f"run: {type(exc).__name__}")
                    log.error("worker.job_escaped_error", exc_info=exc)

            try:
                result = await self._drain()
                stats.callbacks_delivered += int(getattr(result, "delivered", 0) or 0)
            except Exception as exc:
                stats.errors.append(f"drain: {type(exc).__name__}")
                log.error("worker.drain_failed", detail=f"{type(exc).__name__}")

            if (
                self._reconcile is not None
                and self._reconcile_every > 0
                and stats.ticks % self._reconcile_every == 0
            ):
                try:
                    await self._reconcile()
                    stats.reconcile_runs += 1
                except Exception as exc:
                    stats.errors.append(f"reconcile: {type(exc).__name__}")
                    log.error("worker.reconcile_failed", detail=f"{type(exc).__name__}")

            if claimed is None and not self._stopping.is_set():
                # Nothing to do. Sleep rather than spin.
                await self._sleep(self._idle_sleep)

        await self._final_drain(stats)
        log.info(
            "worker.stopped",
            worker=self._worker,
            ticks=stats.ticks,
            jobs_run=stats.jobs_run,
        )
        return stats

    async def _final_drain(self, stats: LoopStats) -> None:
        """One last outbox drain on the way out.

        Purely an optimisation: it delivers callbacks that are already durable, so skipping
        it would only delay them until another worker or the reconciler picks them up.
        """
        self._draining = True
        try:
            result = await self._drain()
            stats.callbacks_delivered += int(getattr(result, "delivered", 0) or 0)
        except Exception as exc:
            stats.errors.append(f"final_drain: {type(exc).__name__}")
            log.warning("worker.final_drain_failed", detail=f"{type(exc).__name__}")
        finally:
            self._draining = False

    async def report_queue_depth(self, job_type: str = "all") -> None:
        """Best-effort gauge update. Never allowed to fail the loop."""
        try:
            pending = await self._store.pending_callbacks(limit=10_000)
            self._metrics.set(QUEUE_DEPTH, float(len(pending)), {"job_type": job_type})
        except Exception:  # pragma: no cover - observability must not break work
            pass
