"""The verified tier's visual verifier (spec 022), and how the worker builds it."""

from __future__ import annotations

import httpx

from ai_engine.domain.errors import ModelUnavailableError
from ai_engine.observability.metrics import Metrics
from ai_engine.ports.cache import ProviderCache

from .providers import GeminiBlindReader, GeminiJson, GeminiReviewer, LightOnCropReader
from .visual import VisualVerifier

__all__ = ["VisualVerifier", "build_visual_verifier"]


def build_visual_verifier(
    settings,
    *,
    client: httpx.Client | None = None,
    cache: ProviderCache | None = None,
    metrics: Metrics | None = None,
) -> VisualVerifier:
    """Wire the real providers. Refuses to build without both keys, so a misconfigured
    worker fails at startup instead of failing every verified page."""
    gemini_key = settings.gemini_api_key.get_secret_value()
    lighton_key = settings.lighton_api_key.get_secret_value()
    missing = [
        n for n, k in (("GEMINI_API_KEY", gemini_key), ("LIGHTON_API_KEY", lighton_key)) if not k
    ]
    if missing:
        raise ModelUnavailableError(f"VERIFIER=visual needs {', '.join(missing)}")
    client = client or httpx.Client(
        timeout=httpx.Timeout(
            settings.http_read_timeout_seconds, connect=settings.http_connect_timeout_seconds
        ),
        follow_redirects=False,
    )
    gemini = GeminiJson(
        client=client,
        api_key=gemini_key,
        model_id=settings.verifier_gemini_model,
        cache=cache,
        metrics=metrics,
    )
    lighton = LightOnCropReader(
        client=client,
        api_key=lighton_key,
        base_url=settings.lighton_base_url,
        cache=cache,
        metrics=metrics,
    )
    return VisualVerifier(
        reviewer=GeminiReviewer(gemini),
        blind_reader=GeminiBlindReader(gemini),
        crop_reader=lighton,
        suspicion_threshold=settings.verifier_suspicion_threshold,
        accept_threshold=settings.verifier_accept_threshold,
        review_threshold=settings.verifier_review_threshold,
        max_suspects=settings.verifier_max_suspects,
        models={"gemini": gemini.model_id, "lighton": lighton.model_id},
        call_counters={"gemini": gemini, "lighton": lighton},
    )
