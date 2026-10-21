"""Metrics facade.

`prometheus_client` is an optional dependency, so this module degrades to a working
in-process counter registry when it is absent. That keeps the metric NAMES -- which several
documents and alert rules refer to -- authoritative and importable regardless of what is
installed.

Names are fixed here and must not be changed without updating
docs/backend-v1/13-observability.md section 3.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any

try:  # pragma: no cover - exercised by whichever branch the environment provides
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
    from prometheus_client import generate_latest as _generate_latest

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROMETHEUS_AVAILABLE = False


# ── Metric names. Referenced by alert rules; treat as a contract. ────────────────
JOBS_ACCEPTED = "ai_engine_jobs_accepted_total"
JOB_DURATION = "ai_engine_job_duration_seconds"
PAGES_TOTAL = "ai_engine_pages_total"
PAGE_DURATION = "ai_engine_page_duration_seconds"
QUEUE_DEPTH = "ai_engine_queue_depth"
QUEUE_OLDEST_AGE = "ai_engine_queue_oldest_age_seconds"
INFERENCE_DURATION = "ai_engine_inference_duration_seconds"
INFERENCE_FAILURES = "ai_engine_inference_failures_total"
INFERENCE_CALLS = "ai_engine_inference_calls_total"
EXTERNAL_CALL_DURATION = "ai_engine_external_call_duration_seconds"
CALLBACK_ATTEMPTS = "ai_engine_callback_attempts_total"
OUTBOX_PENDING = "ai_engine_outbox_pending"
TOKEN_REFRESH = "ai_engine_token_refresh_total"
ALIGNMENT_ANCHOR_RATIO = "ai_engine_alignment_anchor_ratio"
DIACRITIZATION_REVIEW_RATIO = "ai_engine_diacritization_review_ratio"
DIACRITIZATION_TOTAL = "ai_engine_diacritization_total"
PROVIDER_CACHE = "ai_engine_provider_cache_total"
COST_USD = "ai_engine_cost_usd_total"
MODEL_LOAD_SECONDS = "ai_engine_model_load_seconds"
BUDGET_EXHAUSTED = "ai_engine_provider_budget_exhausted_total"
BUDGET_REMAINING = "ai_engine_provider_budget_remaining"


class _FallbackMetrics:
    """A minimal registry used when prometheus_client is not installed.

    Enough to keep call sites working and to let tests assert on counter values.
    """

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._observations: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = (
            defaultdict(list)
        )
        self._lock = Lock()

    @staticmethod
    def _key(name: str, labels: dict[str, Any] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        items = tuple(sorted((k, str(v)) for k, v in (labels or {}).items()))
        return name, items

    def inc(self, name: str, labels: dict[str, Any] | None = None, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += amount

    def set(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._observations[self._key(name, labels)].append(value)

    def counter_value(self, name: str, labels: dict[str, Any] | None = None) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    def observations(self, name: str, labels: dict[str, Any] | None = None) -> list[float]:
        return list(self._observations.get(self._key(name, labels), []))

    def render(self) -> bytes:
        lines = ["# prometheus_client is not installed; fallback registry"]
        for (name, labels), value in sorted(self._counters.items()):
            label_str = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{label_str}}} {value}")
        for (name, labels), value in sorted(self._gauges.items()):
            label_str = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{label_str}}} {value}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._observations.clear()


class Metrics:
    """The public metrics interface used by the rest of the service."""

    def __init__(self) -> None:
        self._fallback = _FallbackMetrics()
        self._registry: Any = None
        self._prom: dict[str, Any] = {}
        if PROMETHEUS_AVAILABLE:
            self._registry = CollectorRegistry()
            self._prom = {
                JOBS_ACCEPTED: Counter(
                    JOBS_ACCEPTED,
                    "Jobs accepted",
                    ["job_type", "outcome"],
                    registry=self._registry,
                ),
                PAGES_TOTAL: Counter(
                    PAGES_TOTAL,
                    "Page terminal reports",
                    ["job_type", "outcome"],
                    registry=self._registry,
                ),
                INFERENCE_CALLS: Counter(
                    INFERENCE_CALLS,
                    "Paid inference calls",
                    ["provider", "model", "stage"],
                    registry=self._registry,
                ),
                INFERENCE_FAILURES: Counter(
                    INFERENCE_FAILURES,
                    "Inference failures",
                    ["provider", "model", "error_code"],
                    registry=self._registry,
                ),
                CALLBACK_ATTEMPTS: Counter(
                    CALLBACK_ATTEMPTS,
                    "Callback delivery attempts",
                    ["endpoint", "outcome"],
                    registry=self._registry,
                ),
                TOKEN_REFRESH: Counter(
                    TOKEN_REFRESH,
                    "Backend token refreshes",
                    ["outcome"],
                    registry=self._registry,
                ),
                OUTBOX_PENDING: Gauge(
                    OUTBOX_PENDING, "Undelivered callbacks", registry=self._registry
                ),
                QUEUE_DEPTH: Gauge(
                    QUEUE_DEPTH, "Queued jobs", ["job_type"], registry=self._registry
                ),
                PAGE_DURATION: Histogram(
                    PAGE_DURATION,
                    "Page stage duration",
                    ["job_type", "stage"],
                    registry=self._registry,
                ),
                COST_USD: Counter(
                    COST_USD,
                    "Estimated provider spend in USD (priced calls only; see /ops/cost)",
                    ["provider", "model", "tier"],
                    registry=self._registry,
                ),
                PROVIDER_CACHE: Counter(
                    PROVIDER_CACHE,
                    "Paid-call cache lookups",
                    ["operation", "outcome"],
                    registry=self._registry,
                ),
                DIACRITIZATION_TOTAL: Counter(
                    DIACRITIZATION_TOTAL,
                    "Job A pages by diacritisation outcome",
                    ["outcome"],
                    registry=self._registry,
                ),
                BUDGET_EXHAUSTED: Counter(
                    BUDGET_EXHAUSTED,
                    "Paid calls refused because a provider window was exhausted",
                    ["provider"],
                    registry=self._registry,
                ),
                BUDGET_REMAINING: Gauge(
                    BUDGET_REMAINING,
                    "Calls left in the current provider window",
                    ["provider"],
                    registry=self._registry,
                ),
                ALIGNMENT_ANCHOR_RATIO: Histogram(
                    ALIGNMENT_ANCHOR_RATIO,
                    "Anchored-token ratio per page",
                    ["align_method", "tts_model"],
                    buckets=(0.1, 0.25, 0.5, 0.6, 0.7, 0.78, 0.85, 0.9, 0.95, 1.0),
                    registry=self._registry,
                ),
            }

    def inc(self, name: str, labels: dict[str, Any] | None = None, amount: float = 1.0) -> None:
        metric = self._prom.get(name)
        if metric is not None:
            (metric.labels(**labels) if labels else metric).inc(amount)
        self._fallback.inc(name, labels, amount)

    def set(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        metric = self._prom.get(name)
        if metric is not None:
            (metric.labels(**labels) if labels else metric).set(value)
        self._fallback.set(name, value, labels)

    def observe(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        metric = self._prom.get(name)
        if metric is not None:
            (metric.labels(**labels) if labels else metric).observe(value)
        self._fallback.observe(name, value, labels)

    def render(self) -> tuple[bytes, str]:
        """Body and content type for `GET /metrics`."""
        if PROMETHEUS_AVAILABLE and self._registry is not None:
            return _generate_latest(self._registry), "text/plain; version=0.0.4; charset=utf-8"
        return self._fallback.render(), "text/plain; charset=utf-8"

    # Test helpers -----------------------------------------------------------------
    def counter_value(self, name: str, labels: dict[str, Any] | None = None) -> float:
        return self._fallback.counter_value(name, labels)

    def observations(self, name: str, labels: dict[str, Any] | None = None) -> list[float]:
        return self._fallback.observations(name, labels)

    def reset(self) -> None:
        self._fallback.reset()


#: One registry per process. Injected into the API via app state.
METRICS = Metrics()
