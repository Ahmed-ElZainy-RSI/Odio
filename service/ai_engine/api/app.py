"""FastAPI application factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from ai_engine.adapters.persistence.factory import open_job_store
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.adapters.persistence.rate_budget import UnlimitedBudget, build_rate_budget
from ai_engine.adapters.persistence.usage_store import InMemoryUsageSink, build_usage_sink
from ai_engine.api.errors import install_error_handlers
from ai_engine.api.routers import jobs, ops
from ai_engine.config.settings import Settings, get_settings
from ai_engine.observability.context import bind
from ai_engine.observability.logging import configure_logging, get_logger
from ai_engine.observability.metrics import METRICS, Metrics
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget

log = get_logger(__name__)

DESCRIPTION = """
The Reading First AI Engine: OCR and audio generation, driven as a job queue by the
Backend's book APIs.

`POST /api/jobs` is the only endpoint in the V1 contract. It returns `202 Accepted` and
performs no AI work -- extraction, synthesis, alignment, artifact upload and progress
callbacks all happen in a worker process afterwards.
""".strip()


def create_app(
    *,
    settings: Settings | None = None,
    store: JobStore | None = None,
    metrics: Metrics | None = None,
    budget: RateBudget | None = None,
    usage: object | None = None,
) -> FastAPI:
    """Build the app.

    Collaborators are injected rather than imported as globals, which is what lets the
    test suite run the real application against an in-memory store.
    """
    resolved_settings = settings or get_settings()
    resolved_metrics = metrics or METRICS
    # An injected store means the caller owns the lifecycle (that is how the tests run the
    # real app against an in-memory store). With nothing injected the app opens the
    # *configured* store in its own lifespan -- it must not fall back to an in-memory one,
    # because that would accept jobs into a store no worker can read.
    owns_store = store is None
    resolved_store: JobStore = store or InMemoryJobStore()
    # The API reports budget usage on `/ops/budget` but never spends it: only workers make
    # paid calls. It is built from settings only when we also own the store, so that the
    # counter and the ledger always come from the same database.
    resolved_budget: RateBudget = budget or UnlimitedBudget()

    configure_logging(
        level=resolved_settings.ai_engine_log_level,
        json_logs=resolved_settings.ai_engine_json_logs,
        role=resolved_settings.ai_engine_role.value,
        version=resolved_settings.ai_engine_version,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if owns_store:
            # Opened here, not in `create_app`, because the store's lock binds to the loop
            # that first uses it -- which is this one.
            app.state.store = await open_job_store(resolved_settings)
            if budget is None:
                app.state.budget = build_rate_budget(
                    settings=resolved_settings, metrics=resolved_metrics
                )
            if usage is None:
                # read-only here: workers write usage, /ops/cost reports it
                app.state.usage = build_usage_sink(settings=resolved_settings)
        log.info(
            "service.starting",
            role=resolved_settings.ai_engine_role.value,
            port=resolved_settings.ai_engine_port,
            store=type(app.state.store).__name__,
            auth_enforce=resolved_settings.auth_enforce,
            verified_tier=resolved_settings.enable_verified_tier,
        )
        if not resolved_settings.auth_enforce:
            # Loud, every boot, until Q-01 is answered. This is a security-relevant
            # default and it must not become invisible.
            log.warning(
                "auth.not_enforced",
                detail=(
                    "inbound token validation is disabled (Q-01 unanswered): the V1 spec "
                    "does not state how to validate {ai-engine-bearer-token}"
                ),
            )
        problems = resolved_settings.config_problems()
        if problems:
            # Loud every boot outside production; ENVIRONMENT=production refuses to start.
            log.warning(
                "config.unsafe_for_production",
                environment=resolved_settings.environment,
                problems=problems,
            )
        if not resolved_settings.source_url_allowed_hosts:
            log.warning(
                "source_url.allowlist_empty",
                detail="set SOURCE_URL_ALLOWED_HOSTS before exposing this service",
            )
        if not resolved_settings.source_url_resolve_and_check:
            log.warning(
                "source_url.ssrf_resolve_check_disabled",
                detail=(
                    "SOURCE_URL_RESOLVE_AND_CHECK=false: a source URL that resolves to a "
                    "private or link-local address will be fetched. Local development only."
                ),
            )
        yield
        log.info("service.stopping")
        for owned in (app.state.store, app.state.budget, app.state.usage) if owns_store else ():
            closer = getattr(owned, "close", None)
            if closer is not None:
                await closer()

    app = FastAPI(
        title="Reading First AI Engine",
        version=resolved_settings.ai_engine_version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.store = resolved_store
    app.state.metrics = resolved_metrics
    app.state.budget = resolved_budget
    app.state.usage = usage or InMemoryUsageSink()
    # A verifier is built only when enforcement is on. `api/deps.py::authenticate`
    # refuses every request if enforcement is on without one, so this can never fail open.
    if resolved_settings.auth_enforce:
        import httpx

        from ai_engine.adapters.auth.jwks_verifier import build_verifier

        jwks_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=resolved_settings.http_connect_timeout_seconds,
                read=10.0,
                write=10.0,
                pool=10.0,
            )
        )
        app.state.jwks_http_client = jwks_client
        app.state.token_verifier = build_verifier(resolved_settings, jwks_client)
    else:
        app.state.jwks_http_client = None
        app.state.token_verifier = None

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a request id and bind it for the duration of the request."""
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        with bind(request_id=request_id):
            response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    install_error_handlers(app)
    app.include_router(ops.router)
    app.include_router(jobs.router)
    return app
