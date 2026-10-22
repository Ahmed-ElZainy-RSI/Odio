"""Spec 030: walidapro's contract scenarios, replayed against this service.

Two people implemented the same Backend contract independently
(walidapro/tests/test_ai_contract.py and this service). Wherever the two readings differ,
one of them misread the spec -- and the Backend team will meet whichever one ships. These
tests send walidapro's *literal* request bodies to our API and pin what we do with them, so
each difference is a decision on record (specs/030-contract-replay-walidapro-tests/
differences.md), not something discovered in a joint debugging session.

Scenarios that both implementations agree on are already covered elsewhere; differences.md
maps every walidapro test to its counterpart here.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.conftest import ALLOWED_HOST, job_a_body, job_b_body

# walidapro's bodies, verbatim apart from the URL host (theirs pointed at a mock server)
WALIDAPRO_JOB_A = {
    "jobId": "job-basic-1",
    "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
    "fileId": "file-1",
    "folderId": "folder-1",
    "pathPrefix": "books/1",
    "sourceDownloadUrl": f"https://{ALLOWED_HOST}/files/never-fetched",
}


def errors(response) -> str:
    return str(response.json())


# ── where the two readings DIFFER ───────────────────────────────────────────────


def test_a_non_guid_job_id_is_rejected_here_but_accepted_by_walidapro(client: TestClient) -> None:
    """Spec: `jobId : "<GUID>"`. walidapro accepts "job-basic-1"; we require RFC 4122."""
    response = client.post("/api/jobs", json=WALIDAPRO_JOB_A)
    assert response.status_code == 422
    assert "jobId" in errors(response)
    fixed = WALIDAPRO_JOB_A | {"jobId": str(uuid4())}
    assert client.post("/api/jobs", json=fixed).status_code == 202


def test_folder_and_path_prefix_are_required_here_optional_in_walidapro(client: TestClient) -> None:
    """Spec: every request body lists folderId and pathPrefix (the upload cycle needs both).
    walidapro's later scenarios omit them; we reject that."""
    body = job_a_body(uuid4())
    del body["folderId"], body["pathPrefix"]
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 422
    assert "folderId" in errors(response) and "pathPrefix" in errors(response)


def test_unknown_job_type_is_422_here_400_in_walidapro(client: TestClient) -> None:
    """Spec is silent on error codes. We use 422 for every body validation failure."""
    response = client.post("/api/jobs", json=job_a_body(uuid4(), jobType="NOT_A_REAL_TYPE"))
    assert response.status_code == 422


def test_audio_job_without_page_number_is_422_here_400_in_walidapro(client: TestClient) -> None:
    body = job_b_body(uuid4())
    del body["pageNumber"]
    assert client.post("/api/jobs", json=body).status_code == 422


def test_plain_http_source_is_rejected_here_accepted_by_walidapro(client: TestClient) -> None:
    """walidapro's bodies use "http://x". We require https on an allowlisted host (SSRF)."""
    response = client.post("/api/jobs", json=job_a_body(uuid4(), sourceDownloadUrl="http://x"))
    assert response.status_code == 422


def test_same_job_id_different_payload_is_409_here_202_in_walidapro_and_the_spec(
    client: TestClient,
) -> None:
    """Spec: "if it has [this GUID] then it should return success". We return 409 when the
    payload differs (a refreshed source URL alone is not a difference). OPEN QUESTION for the
    Backend team -- see differences.md D6; this test pins today's behaviour, not a verdict."""
    job_id = uuid4()
    assert client.post("/api/jobs", json=job_a_body(job_id)).status_code == 202
    changed = job_a_body(job_id, fileId="another-file")
    assert client.post("/api/jobs", json=changed).status_code == 409


# ── where they AGREE (smoke-level; full coverage lives in tests/api and tests/e2e) ──


def test_a_new_job_is_202_and_not_a_duplicate(client: TestClient) -> None:
    body = WALIDAPRO_JOB_A | {"jobId": str(uuid4())}
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 202
    assert response.json()["jobId"] == body["jobId"]
    assert response.json()["duplicate"] is False


def test_the_identical_request_twice_is_202_both_times_and_flags_the_duplicate(
    client: TestClient,
) -> None:
    body = WALIDAPRO_JOB_A | {"jobId": str(uuid4())}
    first, second = (client.post("/api/jobs", json=body) for _ in range(2))
    assert (first.status_code, second.status_code) == (202, 202)
    assert (first.json()["duplicate"], second.json()["duplicate"]) == (False, True)


def test_debug_get_of_an_unknown_job_is_404(client: TestClient) -> None:
    assert client.get(f"/api/jobs/{uuid4()}").status_code == 404
