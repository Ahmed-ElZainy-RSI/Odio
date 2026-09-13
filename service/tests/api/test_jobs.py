"""`POST /api/jobs` contract tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.application.accept_job import AcceptJob, AcceptJobCommand
from ai_engine.domain.enums import JobType, QualityTier
from ai_engine.domain.errors import ConflictError
from ai_engine.observability.metrics import JOBS_ACCEPTED, Metrics
from tests.conftest import ALLOWED_HOST, job_a_body, job_b_body

# ── The 202 contract (REQ-JOB-01) ───────────────────────────────────────────────


def test_valid_job_a_returns_202(client: TestClient) -> None:
    response = client.post("/api/jobs", json=job_a_body(uuid4()))
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["duplicate"] is False
    assert "acceptedAt" in body


def test_valid_job_b_returns_202(client: TestClient) -> None:
    response = client.post("/api/jobs", json=job_b_body(uuid4()))
    assert response.status_code == 202
    assert response.json()["duplicate"] is False


def test_response_carries_the_request_id_header(client: TestClient) -> None:
    response = client.post(
        "/api/jobs", json=job_a_body(uuid4()), headers={"X-Request-Id": "abc123"}
    )
    assert response.headers["x-request-id"] == "abc123"


# ── Idempotency (REQ-IDEM-01/02) ────────────────────────────────────────────────


def test_duplicate_job_id_returns_202_and_flags_the_duplicate(
    client: TestClient, store: InMemoryJobStore
) -> None:
    """REQ-IDEM-01: "if already enqueued return 202 accepted"."""
    body = job_a_body(uuid4())
    first = client.post("/api/jobs", json=body)
    second = client.post("/api/jobs", json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_duplicate_job_id_does_not_execute_twice(
    client: TestClient, store: InMemoryJobStore
) -> None:
    body = job_a_body(uuid4())
    for _ in range(5):
        assert client.post("/api/jobs", json=body).status_code == 202
    # One row, not five.
    assert len(store._jobs) == 1


def test_duplicate_with_a_refreshed_source_url_is_not_a_conflict(
    client: TestClient,
) -> None:
    """A presigned URL legitimately changes between retries.

    REQ-SRC-02 exists precisely because these URLs expire, so a re-delivery carrying a
    fresh URL is the *normal* case and must not be rejected.
    """
    job_id = uuid4()
    first = client.post("/api/jobs", json=job_a_body(job_id))
    second = client.post(
        "/api/jobs",
        json=job_a_body(
            job_id,
            sourceDownloadUrl=f"https://{ALLOWED_HOST}/uploads/book.pdf?X-Amz-Signature=NEW",
        ),
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["duplicate"] is True


def test_duplicate_job_id_with_different_payload_returns_409(client: TestClient) -> None:
    """Returning 202 here would silently drop the new instruction while implying success."""
    job_id = uuid4()
    client.post("/api/jobs", json=job_a_body(job_id))
    response = client.post("/api/jobs", json=job_a_body(job_id, fileId="a-different-file"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_concurrent_identical_accepts_create_exactly_one_job() -> None:
    """The accept must not be a check-then-insert.

    Twenty concurrent calls with the same jobId must yield exactly one `created=True`.
    In Postgres this is `INSERT ... ON CONFLICT (job_id) DO NOTHING`; the in-memory store
    holds a lock across the read-and-insert to give the same guarantee.
    """
    store = InMemoryJobStore()
    accept = AcceptJob(store, metrics=Metrics())
    command = AcceptJobCommand(
        job_id=uuid4(),
        job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        file_id="f",
        folder_id="d",
        path_prefix="p",
        source_download_url="https://s.example.com/x.pdf",
    )

    results = await asyncio.gather(*(accept(command) for _ in range(20)))

    assert sum(1 for r in results if not r.duplicate) == 1
    assert sum(1 for r in results if r.duplicate) == 19
    assert len(store._jobs) == 1


async def test_concurrent_conflicting_accepts_surface_the_conflict() -> None:
    store = InMemoryJobStore()
    accept = AcceptJob(store, metrics=Metrics())
    job_id = uuid4()

    def command(file_id: str) -> AcceptJobCommand:
        return AcceptJobCommand(
            job_id=job_id,
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id=file_id,
            folder_id="d",
            path_prefix="p",
            source_download_url="https://s.example.com/x.pdf",
        )

    outcomes = await asyncio.gather(
        *(accept(command(f"file-{i}")) for i in range(5)), return_exceptions=True
    )
    accepted = [o for o in outcomes if not isinstance(o, BaseException)]
    conflicts = [o for o in outcomes if isinstance(o, ConflictError)]
    assert len(accepted) == 1
    assert len(conflicts) == 4


# ── Validation (REQ-JOB-02/03) ──────────────────────────────────────────────────


def test_unknown_job_type_is_rejected(client: TestClient) -> None:
    """An unknown type accepted with 202 would never run, silently stranding the book."""
    response = client.post("/api/jobs", json=job_a_body(uuid4(), jobType="SOMETHING_ELSE"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_job_id_must_be_a_uuid(client: TestClient) -> None:
    body = job_a_body(uuid4())
    body["jobId"] = "not-a-guid"
    assert client.post("/api/jobs", json=body).status_code == 422


def test_page_number_required_for_job_b(client: TestClient) -> None:
    body = job_b_body(uuid4())
    del body["pageNumber"]
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 422


def test_page_number_forbidden_for_job_a(client: TestClient) -> None:
    response = client.post("/api/jobs", json=job_a_body(uuid4(), pageNumber=3))
    assert response.status_code == 422


def test_page_number_must_be_positive(client: TestClient) -> None:
    """ASSUMPTION A1 / Q-05: pageNumber is 1-based."""
    assert client.post("/api/jobs", json=job_b_body(uuid4(), pageNumber=0)).status_code == 422
    assert client.post("/api/jobs", json=job_b_body(uuid4(), pageNumber=-1)).status_code == 422


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    """Silently ignoring an unexpected field hides a contract mismatch."""
    response = client.post("/api/jobs", json=job_a_body(uuid4(), surprise="value"))
    assert response.status_code == 422


def test_missing_required_field_is_rejected(client: TestClient) -> None:
    body = job_a_body(uuid4())
    del body["folderId"]
    assert client.post("/api/jobs", json=body).status_code == 422


# ── SSRF controls ───────────────────────────────────────────────────────────────


def test_source_url_must_be_https(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        json=job_a_body(uuid4(), sourceDownloadUrl=f"http://{ALLOWED_HOST}/x.pdf"),
    )
    assert response.status_code == 422


def test_source_url_host_must_be_allowlisted(client: TestClient) -> None:
    """`sourceDownloadUrl` is caller-supplied and fetched server-side, so an unrestricted
    engine is an open proxy into the worker's network."""
    response = client.post(
        "/api/jobs",
        json=job_a_body(uuid4(), sourceDownloadUrl="https://evil.example.net/x.pdf"),
    )
    assert response.status_code == 422
    assert "allowlist" in response.json()["error"]["message"]


def test_source_url_subdomain_of_allowlisted_host_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        json=job_a_body(uuid4(), sourceDownloadUrl=f"https://eu.{ALLOWED_HOST}/x.pdf"),
    )
    assert response.status_code == 202


@pytest.mark.parametrize("blocked", ["localhost", "127.0.0.1", "metadata.google.internal"])
def test_loopback_and_metadata_hosts_are_always_blocked(client: TestClient, blocked: str) -> None:
    """Defence in depth: blocked even if someone adds them to the allowlist by mistake."""
    response = client.post(
        "/api/jobs", json=job_a_body(uuid4(), sourceDownloadUrl=f"https://{blocked}/x.pdf")
    )
    assert response.status_code == 422


@pytest.mark.parametrize("traversal", ["../../etc", "/absolute", "a\x00b"])
def test_path_traversal_in_folder_fields_is_rejected(client: TestClient, traversal: str) -> None:
    assert client.post("/api/jobs", json=job_a_body(uuid4(), folderId=traversal)).status_code == 422


# ── Quality tier kill-switch ────────────────────────────────────────────────────


async def test_verified_tier_is_downgraded_while_the_kill_switch_is_off() -> None:
    """A 40-100x cost multiplier must not be reachable by a request alone."""
    store = InMemoryJobStore()
    accept = AcceptJob(store, metrics=Metrics(), enable_verified_tier=False)
    job_id = uuid4()
    await accept(
        AcceptJobCommand(
            job_id=job_id,
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id="f",
            folder_id="d",
            path_prefix="p",
            source_download_url="https://s.example.com/x.pdf",
            quality_tier=QualityTier.VERIFIED,
        )
    )
    job = await store.get(job_id)
    assert job is not None
    assert job.quality_tier is QualityTier.FAST


async def test_verified_tier_is_honoured_when_enabled() -> None:
    store = InMemoryJobStore()
    accept = AcceptJob(store, metrics=Metrics(), enable_verified_tier=True)
    job_id = uuid4()
    await accept(
        AcceptJobCommand(
            job_id=job_id,
            job_type=JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
            file_id="f",
            folder_id="d",
            path_prefix="p",
            source_download_url="https://s.example.com/x.pdf",
            quality_tier=QualityTier.VERIFIED,
        )
    )
    job = await store.get(job_id)
    assert job is not None
    assert job.quality_tier is QualityTier.VERIFIED


# ── Metrics ─────────────────────────────────────────────────────────────────────


def test_accept_metrics_distinguish_new_from_duplicate(
    client: TestClient, metrics: Metrics
) -> None:
    body = job_a_body(uuid4())
    client.post("/api/jobs", json=body)
    client.post("/api/jobs", json=body)

    labels = {"job_type": "PAGES_IMG_AND_TEXT_EXTRACTION"}
    assert metrics.counter_value(JOBS_ACCEPTED, {**labels, "outcome": "new"}) == 1
    assert metrics.counter_value(JOBS_ACCEPTED, {**labels, "outcome": "duplicate"}) == 1


# ── Job inspection (ops) ────────────────────────────────────────────────────────


def test_job_status_reports_pages_awaiting_report(
    client: TestClient, store: InMemoryJobStore
) -> None:
    """The one-request answer to "which pages has the Backend not heard about?"."""
    job_id = uuid4()
    client.post("/api/jobs", json=job_a_body(job_id))
    asyncio.run(store.fan_out_pages(job_id, 3))

    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["pagesCount"] == 3
    assert body["pages"]["pending"] == 3
    assert body["pages"]["nonTerminal"] == [1, 2, 3]


def test_job_status_404_for_unknown_job(client: TestClient) -> None:
    response = client.get(f"/api/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_job_status_never_leaks_the_source_url(client: TestClient, store: InMemoryJobStore) -> None:
    """A presigned URL is a bearer credential and must not appear in any response."""
    job_id = uuid4()
    client.post("/api/jobs", json=job_a_body(job_id))
    text = client.get(f"/api/jobs/{job_id}").text
    assert "X-Amz-Signature" not in text
    assert ALLOWED_HOST not in text


# ── Honesty when the store is unavailable ───────────────────────────────────────


def test_postgres_down_returns_503_not_202(settings, metrics: Metrics) -> None:
    """We must never claim to have accepted a job we could not persist.

    Returning 202 here would make the Backend stop retrying a job that will never run,
    and the book would stall silently and permanently. A 503 makes it retry, which is
    exactly the behaviour we want.
    """
    from fastapi.testclient import TestClient

    from ai_engine.api.app import create_app

    class UnreachableStore(InMemoryJobStore):
        async def accept(self, job):  # type: ignore[no-untyped-def]
            raise ConnectionRefusedError("could not connect to the job store")

    app = create_app(settings=settings, store=UnreachableStore(), metrics=metrics)
    with TestClient(app) as client:
        response = client.post("/api/jobs", json=job_a_body(uuid4()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    # And the failure is visible, not silent.
    assert (
        metrics.counter_value(
            JOBS_ACCEPTED,
            {"job_type": "PAGES_IMG_AND_TEXT_EXTRACTION", "outcome": "rejected"},
        )
        == 1
    )


def test_store_failure_message_leaks_nothing(settings, metrics: Metrics) -> None:
    from fastapi.testclient import TestClient

    from ai_engine.api.app import create_app

    class LeakyStore(InMemoryJobStore):
        async def accept(self, job):  # type: ignore[no-untyped-def]
            raise RuntimeError("connect failed: postgresql://ai_engine:hunter2@db.internal:5432/x")

    app = create_app(settings=settings, store=LeakyStore(), metrics=metrics)
    with TestClient(app) as client:
        body = client.post("/api/jobs", json=job_a_body(uuid4())).json()
    assert "hunter2" not in body["error"]["message"]
    assert "postgresql://" not in body["error"]["message"]
