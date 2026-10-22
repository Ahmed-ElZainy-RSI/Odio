"""Worker composition root.

Wires the adapters into the application layer. This is the only module that knows the
concrete types, which is what lets every layer above it stay testable with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ai_engine.adapters.ai.aligner_whisper import WhisperAligner
from ai_engine.adapters.ai.cached import CachedOcrEngine, CachedTts
from ai_engine.adapters.ai.diacritizer_local import LocalDiacritizer
from ai_engine.adapters.ai.ocr_providers import build_ocr_engine
from ai_engine.adapters.ai.renderer_pymupdf import PyMuPdfRenderer
from ai_engine.adapters.ai.tts import GeminiTts, SyntheticTts
from ai_engine.adapters.ai.verifier import build_visual_verifier
from ai_engine.adapters.ai.verifier_passthrough import PassThroughVerifier
from ai_engine.adapters.backend.files_client import BackendFilesClient
from ai_engine.adapters.backend.jobs_client import BackendJobsClient
from ai_engine.adapters.backend.token_provider import KeycloakTokenProvider
from ai_engine.adapters.http.retry import RetryPolicy
from ai_engine.adapters.persistence.cache_fs import FileProviderCache
from ai_engine.adapters.persistence.rate_budget import build_rate_budget
from ai_engine.adapters.persistence.usage_store import build_usage_sink
from ai_engine.adapters.storage.source_fetcher import PresignedSourceFetcher, SourcePolicy
from ai_engine.application.outbox import OutboxDrainer
from ai_engine.application.reconcile import Reconciler
from ai_engine.application.run_audio_job import AudioJobOutcome, RunAudioJob
from ai_engine.application.run_extraction_job import JobOutcome, RunExtractionJob
from ai_engine.application.run_page_task import PageTaskExecutor
from ai_engine.application.usage_meter import UsageMeter
from ai_engine.application.worker_loop import WorkerLoop
from ai_engine.config.settings import Settings, load_price_table
from ai_engine.domain.enums import JobType
from ai_engine.domain.job import Job
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.job_store import JobStore
from ai_engine.ports.rate_budget import RateBudget

log = get_logger(__name__)


@dataclass
class WorkerContext:
    """Everything a worker needs, assembled."""

    settings: Settings
    store: JobStore
    metrics: Metrics
    extraction: RunExtractionJob
    audio: RunAudioJob
    drainer: OutboxDrainer
    reconciler: Reconciler
    budget: RateBudget
    usage: object = None  # the UsageSink; closed by the process that owns the worker

    async def run_job(self, job: Job) -> JobOutcome | AudioJobOutcome:
        """Run one job of either type to a terminal state, then drain callbacks."""
        if job.job_type is JobType.PAGES_IMG_AND_TEXT_EXTRACTION:
            outcome: JobOutcome | AudioJobOutcome = await self.extraction(job)
        else:
            outcome = await self.audio(job)
        await self.drainer.drain()
        return outcome

    def loop(self, **kwargs: object) -> WorkerLoop:
        """Build the polling loop around this context."""
        return WorkerLoop(
            store=self.store,
            run_job=self.run_job,
            drain=self.drainer.drain,
            reconcile=self._reconcile_running_jobs,
            metrics=self.metrics,
            **kwargs,  # type: ignore[arg-type]
        )

    def reconcile_loop(self, **kwargs: object) -> WorkerLoop:
        """The reconciler role: sweep deadlines and drain callbacks, claim nothing.

        Deliberately the same loop as the worker, minus claiming. Reusing it means the
        reconciler role inherits graceful shutdown, store-outage tolerance and the same
        log events, rather than growing a second, less-tested loop.
        """
        kwargs.setdefault("worker_name", "reconciler")
        kwargs.setdefault("reconcile_every_ticks", 1)
        return WorkerLoop(
            store=self.store,
            run_job=self.run_job,
            drain=self.drainer.drain,
            reconcile=self._reconcile_running_jobs,
            metrics=self.metrics,
            claim_jobs=False,
            **kwargs,  # type: ignore[arg-type]
        )

    async def _reconcile_running_jobs(self) -> object:
        """Feed the reconciler the jobs worth examining."""
        candidates = getattr(self.store, "running_jobs", None)
        jobs = await candidates() if candidates is not None else []
        return await self.reconciler.run(list(jobs))

    async def drain_until_quiet(self, *, rounds: int = 5) -> None:
        for _ in range(rounds):
            result = await self.drainer.drain()
            if result.delivered == 0 and result.failed == 0:
                return


def build_worker(
    *,
    settings: Settings,
    store: JobStore,
    metrics: Metrics,
    http_client: httpx.AsyncClient,
    tts: object | None = None,
    transcriber: object | None = None,
    ocr: object | None = None,
    budget: RateBudget | None = None,
    verifier: object | None = None,
    diacritizer: object | None = None,
    cache: object | None = None,
    usage_sink: object | None = None,
) -> WorkerContext:
    """Compose the worker.

    ``http_client`` is injected so tests can hand in an ASGI transport pointed at the
    contract fake -- which is what makes the whole protocol verifiable offline. ``tts`` and
    ``transcriber`` are injectable for the same reason: Job B can then run end to end with
    zero paid calls while still exercising the real binding logic.
    """
    policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay_seconds,
        max_delay=settings.retry_max_delay_seconds,
        total_budget=settings.retry_total_budget_seconds,
    )
    tokens = KeycloakTokenProvider(
        token_url=settings.token_url,
        client_id=settings.ai_engine_client_id,
        client_secret=settings.ai_engine_client_secret.get_secret_value(),
        client=http_client,
        metrics=metrics,
        policy=policy,
    )
    files = BackendFilesClient(
        base_url=settings.backend_base_url,
        client=http_client,
        tokens=tokens,
        metrics=metrics,
        policy=policy,
    )
    jobs_client = BackendJobsClient(
        base_url=settings.backend_base_url,
        client=http_client,
        tokens=tokens,
        metrics=metrics,
        policy=policy,
    )
    fetcher = PresignedSourceFetcher(
        client=http_client,
        files=files,
        policy=SourcePolicy(
            allowed_hosts=tuple(settings.source_url_allowed_hosts),
            require_https=settings.source_url_require_https,
            max_bytes=settings.max_source_bytes,
            # This must default ON. The API's allowlist runs at accept time and checks the
            # *literal* host; the worker fetches later and over real HTTP, so a hostname
            # that is on the allowlist but resolves to 169.254.169.254 -- a rebind, a
            # misconfigured internal zone -- is only caught here. Tests and local runs
            # against a loopback fake set SOURCE_URL_RESOLVE_AND_CHECK=false.
            resolve_and_check=settings.source_url_resolve_and_check,
        ),
        retry=policy,
    )
    # The provider budget is shared by both job types: Job A's OCR and Job B's TTS draw
    # on the same free-tier allowances.
    budget = budget or build_rate_budget(settings=settings, metrics=metrics)
    # Every paid call's usage, priced for /ops/cost and ai_engine_cost_usd_total (spec 025).
    # Same database as the job store, like the budget.
    usage_sink = usage_sink or build_usage_sink(settings=settings)
    meter = UsageMeter(sink=usage_sink, prices=load_price_table(settings), metrics=metrics)

    renderer = PyMuPdfRenderer()

    _engine_cache: dict[str, object] = {}

    # Paid-call cache (spec 021), opt-in via PROVIDER_CACHE_DIR. Only paid engines are
    # wrapped: the text layer and the synthetic TTS cost nothing to recompute.
    if cache is None and settings.provider_cache_dir is not None:
        cache = FileProviderCache(
            settings.provider_cache_dir, max_age_seconds=settings.provider_cache_max_age_seconds
        )

    def _paid(engine: object) -> bool:
        return getattr(engine, "provider", "local") != "local"

    def ocr_for(pdf_path: str) -> object:
        """Resolve the OCR engine for one document, once.

        `auto` inspects the PDF for a usable text layer, which is free and exact and covers
        25 of the 36 sample books -- so the answer depends on the document and cannot be
        decided at composition time. Cached per path so the inspection happens once per
        job rather than once per page.
        """
        if ocr is not None:
            return ocr
        cached = _engine_cache.get(pdf_path)
        if cached is None:
            cached = build_ocr_engine(
                settings=settings,
                renderer=renderer,
                http_client=http_client,
                metrics=metrics,
                pdf_path=pdf_path,
            )
            if cache is not None and _paid(cached):
                cached = CachedOcrEngine(cached, cache, metrics)
            _engine_cache[pdf_path] = cached
            log.info("ocr.engine_selected", engine=getattr(cached, "name", "?"))
        return cached

    aligner = WhisperAligner(
        legacy_repo_root=settings.legacy_repo_root,
        transcriber=transcriber,
        model_size=settings.align_whisper_model_size,
        device=settings.align_whisper_device,
        compute_type=settings.align_whisper_compute_type,
    )
    if tts is None:
        tts = (
            SyntheticTts()
            if settings.tts_engine == "synthetic"
            else GeminiTts(
                model_id=settings.tts_gemini_model,
                voice=settings.tts_gemini_voice,
                api_key=settings.gemini_api_key.get_secret_value() or None,
            )
        )

    if cache is not None and _paid(tts):
        tts = CachedTts(tts, cache, metrics)

    # The verified tier is wired only while ENABLE_VERIFIED_TIER is on (spec 020); what runs
    # it is VERIFIER (spec 022). Building the visual verifier checks its keys, so a worker
    # missing them fails here rather than on every verified page.
    if not settings.enable_verified_tier:
        verifier = None
    elif verifier is None:
        verifier = (
            build_visual_verifier(settings, cache=cache, metrics=metrics)  # type: ignore[arg-type]
            if settings.verifier == "visual"
            else PassThroughVerifier()
        )

    # Job A diacritisation (spec 016), opt-in. Loading the weights here makes a missing
    # model fail the worker at startup instead of failing every undiacritised page.
    if diacritizer is None and settings.diacritizer == "local":
        diacritizer = LocalDiacritizer()
        diacritizer.load()

    page_executor = PageTaskExecutor(
        store=store,
        renderer=renderer,
        ocr_for=ocr_for,
        uploader=files,
        metrics=metrics,
        page_timeout_seconds=float(settings.page_task_timeout_seconds),
        budget=budget,
        verifier=verifier,
        diacritizer=diacritizer,
        diac_threshold=settings.diac_threshold,
        meter=meter,
    )
    drainer = OutboxDrainer(
        store=store,
        sender=jobs_client,
        metrics=metrics,
        max_attempts=settings.callback_max_attempts,
    )
    extraction = RunExtractionJob(
        store=store,
        fetcher=fetcher,
        renderer=renderer,
        page_executor=page_executor,
        metrics=metrics,
        scratch_root=settings.scratch_dir,
        # The metadata callback gates page work, so the job runner needs to be able to
        # flush the outbox and confirm delivery before fanning out.
        flush_callbacks=drainer.drain,
        max_pages=settings.max_source_pages,
        page_concurrency=settings.job_page_concurrency,
    )
    audio_job = RunAudioJob(
        store=store,
        fetcher=fetcher,
        tts=tts,
        aligner=aligner,
        uploader=files,
        metrics=metrics,
        scratch_root=settings.scratch_dir,
        voice=settings.tts_gemini_voice,
        page_timeout_seconds=float(settings.page_task_timeout_seconds),
        budget=budget,
        meter=meter,
    )
    reconciler = Reconciler(
        store=store,
        metrics=metrics,
        job_deadline_seconds=settings.job_deadline_seconds,
    )
    return WorkerContext(
        settings=settings,
        store=store,
        budget=budget,
        metrics=metrics,
        extraction=extraction,
        audio=audio_job,
        drainer=drainer,
        reconciler=reconciler,
        usage=usage_sink,
    )
