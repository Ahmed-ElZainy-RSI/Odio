"""Spec 020: which pages the verified tier runs on, and what it may produce.

Exercises PageTaskExecutor._produce directly with fakes, so no PDF, network or paid
provider is involved. The worker-level gate (ENABLE_VERIFIED_TIER) is checked through
build_worker.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from ai_engine.adapters.ai.verifier import VisualVerifier
from ai_engine.adapters.ai.verifier_passthrough import PassThroughVerifier
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.application.run_page_task import PageTaskExecutor, PageTaskSpec
from ai_engine.domain.enums import ArtifactType, JobType, QualityTier
from ai_engine.domain.errors import ErrorCode, ModelUnavailableError
from ai_engine.domain.evidence import Evidence
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.domain.tokens import PageProvenance, Token
from ai_engine.domain.verification import (
    Verdict,
    VerificationResult,
    VerifierContractViolation,
)
from ai_engine.observability.metrics import Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings

TOKENS = [Token(id="p001w000", text="ذهب"), Token(id="p001w001", text="الفضل")]


class FakeRenderer:
    def render_page(self, pdf_path: str, page_number: int) -> bytes:
        return b"jpeg-bytes"


class FakeOcr:
    def __init__(self, name: str = "lighton") -> None:
        self.name = name
        self.provider = "local"
        self.model_id = "fake"

    def extract_page(self, *, page_image, pdf_path, page_number):
        return list(TOKENS), PageProvenance(ocr_engine=self.name)


class CorrectingVerifier:
    """Corrects الفضل -> الفصل with an ACCEPT verdict, like a real verifier would."""

    name = "correcting"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def verify(self, *, page_image, tokens, page_number):
        self.calls.append({"image": page_image, "page": page_number, "n": len(tokens)})
        fixed = [tokens[0], replace(tokens[1], text="الفصل")]
        evidence = Evidence(
            token_id="p001w001",
            original="الفضل",
            candidate="الفصل",
            applied=True,
            status="ACCEPT",
            detector="verifier",
            decided_by="verifier",
            rule="decide()",
        )
        return VerificationResult(
            tokens=fixed, verdicts=[Verdict(evidence)], calls_made={"gemini": 3}
        )


class RenumberingVerifier:
    name = "renumbering"

    def verify(self, *, page_image, tokens, page_number):
        return VerificationResult(
            tokens=[replace(t, id=f"p001w9{i:02d}") for i, t in enumerate(tokens)]
        )


def job(tier: QualityTier) -> Job:
    return Job(
        job_id=uuid4(),
        job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        file_id="book-1",
        folder_id="books/g1",
        path_prefix="s3/2026/10",
        source_download_url=f"{BACKEND}/_source/book-1",
        payload_hash=canonical_payload_hash(
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id="book-1",
            folder_id="books/g1",
            path_prefix="s3/2026/10",
            page_number=None,
        ),
        quality_tier=tier,
    )


def produce(tmp_path: Path, tier: QualityTier, *, verifier=None, ocr=None) -> str:
    executor = PageTaskExecutor(
        store=InMemoryJobStore(),
        renderer=FakeRenderer(),
        ocr=ocr or FakeOcr(),
        uploader=None,  # type: ignore[arg-type]  # _produce never uploads
        metrics=Metrics(),
        verifier=verifier,
    )
    spec = PageTaskSpec(job=job(tier), page_number=1, source_path="book.pdf", scratch_dir=tmp_path)
    artifacts = executor._produce(spec)
    return artifacts[ArtifactType.TEXT].payload.decode("utf-8")


def test_verified_job_runs_the_verifier_and_ships_its_tokens(tmp_path: Path) -> None:
    verifier = CorrectingVerifier()
    text = produce(tmp_path, QualityTier.VERIFIED, verifier=verifier)
    assert "الفصل" in text and "الفضل" not in text
    assert verifier.calls == [{"image": b"jpeg-bytes", "page": 1, "n": 2}]


def test_fast_job_never_calls_the_verifier(tmp_path: Path) -> None:
    verifier = CorrectingVerifier()
    text = produce(tmp_path, QualityTier.FAST, verifier=verifier)
    assert "الفضل" in text
    assert verifier.calls == []


def test_text_layer_pages_are_not_verified(tmp_path: Path) -> None:
    verifier = CorrectingVerifier()
    produce(tmp_path, QualityTier.VERIFIED, verifier=verifier, ocr=FakeOcr("pdf-text-layer"))
    assert verifier.calls == []


def test_verified_job_without_a_verifier_falls_back_to_fast(tmp_path: Path) -> None:
    assert "الفضل" in produce(tmp_path, QualityTier.VERIFIED, verifier=None)


def test_a_verifier_that_breaks_the_contract_fails_the_page_as_internal(tmp_path: Path) -> None:
    with pytest.raises(VerifierContractViolation, match="renumbering broke its contract") as info:
        produce(tmp_path, QualityTier.VERIFIED, verifier=RenumberingVerifier())
    assert info.value.reason.code is ErrorCode.INTERNAL_ERROR


def test_passthrough_verified_output_equals_fast_output(tmp_path: Path) -> None:
    fast = produce(tmp_path, QualityTier.FAST)
    verified = produce(tmp_path, QualityTier.VERIFIED, verifier=PassThroughVerifier())
    assert verified == fast


@pytest.mark.parametrize("enabled", [False, True])
async def test_worker_wires_a_verifier_only_when_the_tier_is_enabled(
    tmp_path: Path, enabled: bool
) -> None:
    settings = make_settings(tmp_path).model_copy(update={"enable_verified_tier": enabled})
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        worker = build_worker(
            settings=settings,
            store=InMemoryJobStore(),
            metrics=Metrics(),
            http_client=client,
            verifier=CorrectingVerifier(),
        )
    wired = worker.extraction._pages._verifier
    if enabled:
        assert isinstance(wired, CorrectingVerifier)
    else:
        assert wired is None, "a verifier must not be wired while the tier is disabled"


async def test_worker_wires_the_passthrough_when_asked(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(
        update={"enable_verified_tier": True, "verifier": "passthrough"}
    )
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        worker = build_worker(
            settings=settings, store=InMemoryJobStore(), metrics=Metrics(), http_client=client
        )
    assert isinstance(worker.extraction._pages._verifier, PassThroughVerifier)


async def test_worker_wires_the_visual_verifier_by_default(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(
        update={
            "enable_verified_tier": True,
            "gemini_api_key": SecretStr("g"),
            "lighton_api_key": SecretStr("l"),
        }
    )
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        worker = build_worker(
            settings=settings, store=InMemoryJobStore(), metrics=Metrics(), http_client=client
        )
    assert isinstance(worker.extraction._pages._verifier, VisualVerifier)


async def test_the_visual_verifier_without_keys_fails_the_worker_at_startup(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(update={"enable_verified_tier": True})
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        with pytest.raises(ModelUnavailableError, match="GEMINI_API_KEY"):
            build_worker(
                settings=settings, store=InMemoryJobStore(), metrics=Metrics(), http_client=client
            )


async def test_the_visual_verifier_shares_the_provider_cache(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(
        update={
            "enable_verified_tier": True,
            "gemini_api_key": SecretStr("g"),
            "lighton_api_key": SecretStr("l"),
        }
    )
    cache = object()
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        worker = build_worker(
            settings=settings,
            store=InMemoryJobStore(),
            metrics=Metrics(),
            http_client=client,
            cache=cache,
        )
    verifier = worker.extraction._pages._verifier
    assert verifier._crop_reader._cache._cache is cache
    assert verifier._blind._gemini._cache._cache is cache
