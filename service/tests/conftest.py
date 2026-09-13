"""Shared fixtures.

Two properties are enforced structurally, mirroring the existing suite's approach:
  * ZERO network calls -- nothing here reaches a real provider, Backend or Keycloak.
  * ZERO paid API calls -- no AI adapter is constructed at all in this slice.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.api.app import create_app
from ai_engine.config.settings import Settings
from ai_engine.observability.metrics import Metrics

ALLOWED_HOST = "storage.example.com"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ai_engine_json_logs=False,
        ai_engine_log_level="WARNING",
        source_url_allowed_hosts=[ALLOWED_HOST],
        auth_enforce=False,
        enable_verified_tier=False,
        # Pinned, not `auto`: the fixtures build tiny PDFs that fall under the text-layer
        # word threshold, so `auto` would resolve to a paid provider. This still exercises
        # `build_ocr_engine`'s dispatch -- just deterministically and for free.
        ocr_engine="textlayer",
    )


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def client(settings: Settings, store: InMemoryJobStore, metrics: Metrics) -> Iterator[TestClient]:
    app = create_app(settings=settings, store=store, metrics=metrics)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def job_id() -> UUID:
    return uuid4()


def job_a_body(job_id: UUID, **overrides: object) -> dict:
    body = {
        "jobId": str(job_id),
        "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
        "fileId": "b7d1e2c4-0a11-4c3e-9f2b-77c1a9e4d001",
        "folderId": "books/reading-first/grade-1",
        "pathPrefix": "s3-prefix/2026/09",
        "sourceDownloadUrl": f"https://{ALLOWED_HOST}/uploads/book.pdf?X-Amz-Signature=abc",
    }
    body.update(overrides)  # type: ignore[arg-type]
    return body


def job_b_body(job_id: UUID, **overrides: object) -> dict:
    body = {
        "jobId": str(job_id),
        "jobType": "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
        "fileId": "c1a2b3d4-5566-4778-99aa-bbccddeeff00",
        "folderId": "books/reading-first/grade-1",
        "pathPrefix": "s3-prefix/2026/09",
        "sourceDownloadUrl": f"https://{ALLOWED_HOST}/text/p007.txt?X-Amz-Signature=abc",
        "pageNumber": 7,
    }
    body.update(overrides)  # type: ignore[arg-type]
    return body
