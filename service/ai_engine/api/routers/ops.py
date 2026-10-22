"""Operational endpoints. PROPOSAL -- not in the V1 PDF, but required to run a container."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from ai_engine.api.deps import get_budget, get_metrics, get_settings_dep, get_store, get_usage
from ai_engine.api.schemas import (
    BudgetResponse,
    BudgetWindow,
    CostLine,
    CostResponse,
    DependencyCheck,
    HealthResponse,
    ReadyResponse,
)
from ai_engine.config.settings import Settings, load_price_table
from ai_engine.domain.usage import summarize
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget
from ai_engine.ports.usage import UsageSink

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> HealthResponse:
    """Always `200` while the process is alive. Performs NO dependency checks.

    Checking a dependency here would restart every container during a brief Postgres
    outage, which is strictly worse than serving 503 from `/ready`.
    """
    return HealthResponse(
        status="ok",
        role=settings.ai_engine_role.value,
        version=settings.ai_engine_version,
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness")
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    store: Annotated[JobStore, Depends(get_store)],
) -> ReadyResponse:
    """Report per-dependency readiness.

    Gemini and LightOn reachability are deliberately NOT checked: during a provider outage,
    accepting jobs into a durable queue is still the correct behaviour, so removing the API
    from the load balancer would make things worse. Provider health belongs in alerts.
    """
    checks: dict[str, DependencyCheck] = {}

    try:
        await store.get(__import__("uuid").UUID(int=0))
        checks["job_store"] = DependencyCheck(ok=True)
    except Exception as exc:
        checks["job_store"] = DependencyCheck(ok=False, detail=type(exc).__name__)

    if settings.auth_enforce:
        configured = bool(settings.backend_allowed_client_ids)
        checks["inbound_auth"] = DependencyCheck(
            ok=configured,
            detail=None if configured else "BACKEND_ALLOWED_CLIENT_IDS is empty",
        )
    else:
        checks["inbound_auth"] = DependencyCheck(ok=True, detail="not enforced (blocked on Q-01)")

    # Spec 031: in production these can't exist (the settings refuse to load), so here they
    # are informational -- visible, but not a reason to pull a dev instance from rotation.
    problems = settings.config_problems()
    checks["config"] = DependencyCheck(
        ok=True,
        detail=(
            f"{settings.environment}: {len(problems)} setting(s) unsafe for production: "
            + "; ".join(problems)
        )
        if problems
        else f"{settings.environment}: production-safe",
    )

    all_ok = all(check.ok for check in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status="ready" if all_ok else "not_ready",
        role=settings.ai_engine_role.value,
        checks=checks,
    )


@router.get("/metrics", summary="Prometheus exposition")
async def metrics(metrics: Annotated[Metrics, Depends(get_metrics)]) -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


@router.get("/ops/budget", response_model=BudgetResponse, summary="Provider budget usage")
async def budget(
    budget: Annotated[RateBudget, Depends(get_budget)],
) -> BudgetResponse:
    """How much of each provider allowance is left in the current window.

    Worth an endpoint because the failure it describes is invisible otherwise: jobs stop
    making progress and nothing looks broken. `remaining == 0` here explains it exactly.
    """
    usage = await budget.usage()
    return BudgetResponse(
        windows=[
            BudgetWindow(
                provider=entry.provider,
                used=entry.used,
                remaining=entry.remaining,
                maxCalls=entry.max_calls,
                windowStart=entry.window_start,
                windowReset=entry.window_reset,
            )
            for entry in usage
        ]
    )


@router.get(
    "/ops/cost",
    response_model=CostResponse,
    response_model_by_alias=True,
    summary="Recorded provider usage and its estimated cost",
)
async def cost(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    sink: Annotated[UsageSink, Depends(get_usage)],
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
) -> CostResponse:
    """Calls, units and USD per provider/model/operation, for one job or everything.

    Priced with the configured table (PRICES_FILE, or config/prices.toml). Calls it cannot
    price are counted in `unpricedCalls` and the total is then a lower bound.
    """
    prices = load_price_table(settings)
    records = await sink.records(job_id=job_id)
    lines = summarize(records, prices, by=("provider", "model", "operation"))
    total = summarize(records, prices, by=())
    whole = total.get(())
    return CostResponse(
        job_id=job_id,
        prices_as_of=prices.as_of,
        calls=len(records),
        usd=round(whole.usd, 6) if whole else 0.0,
        unpriced_calls=whole.unpriced if whole else 0,
        lines=[
            CostLine(
                provider=k[0],
                model=k[1],
                operation=k[2],
                calls=v.calls,
                cached=v.cached,
                unpriced=v.unpriced,
                usd=round(v.usd, 6),
                input_tokens=v.input_tokens,
                output_tokens=v.output_tokens,
                chars=v.chars,
                pages=v.pages,
            )
            for k, v in sorted(lines.items())
        ],
    )
