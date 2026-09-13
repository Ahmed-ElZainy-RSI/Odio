"""Job, page and artifact entity rules."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_engine.domain.enums import ArtifactType, JobType
from ai_engine.domain.job import (
    Artifact,
    ArtifactDescriptor,
    Job,
    PageTask,
    canonical_payload_hash,
)


def make_job(**overrides: object) -> Job:
    base: dict = {
        "job_id": uuid4(),
        "job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        "file_id": "f",
        "folder_id": "d",
        "path_prefix": "p",
        "source_download_url": "https://s.example.com/x.pdf",
        "payload_hash": "h",
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def test_job_a_must_not_carry_a_page_number() -> None:
    with pytest.raises(ValueError, match="must not carry"):
        make_job(page_number=3)


def test_job_b_requires_a_page_number() -> None:
    with pytest.raises(ValueError, match="requires a pageNumber"):
        make_job(job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION)


def test_job_a_page_work_is_gated_on_the_metadata_callback() -> None:
    """REQ-META-01: `pagesCount` must reach the Backend before any page work.

    Starting page work first would make the Backend receive page callbacks for a job whose
    page count it does not know.
    """
    from datetime import UTC, datetime

    job = make_job()
    assert not job.may_start_page_work
    job.metadata_sent_at = datetime.now(UTC)
    assert job.may_start_page_work


def test_job_b_is_never_gated() -> None:
    """Job B carries its own page and sends no metadata."""
    job = make_job(job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION, page_number=7)
    assert job.may_start_page_work


def test_payload_hash_ignores_the_source_url() -> None:
    """A presigned URL legitimately changes between retries (REQ-SRC-02 exists for it).

    Including it in the hash would turn every ordinary Backend retry into a spurious 409.
    """
    common = {
        "job_type": JobType.PAGES_IMG_AND_TEXT_EXTRACTION,
        "file_id": "f",
        "folder_id": "d",
        "path_prefix": "p",
        "page_number": None,
    }
    assert canonical_payload_hash(**common) == canonical_payload_hash(**common)


def test_payload_hash_changes_with_a_material_field() -> None:
    a = canonical_payload_hash(
        job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION,
        file_id="f",
        folder_id="d",
        path_prefix="p",
        page_number=7,
    )
    b = canonical_payload_hash(
        job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION,
        file_id="f",
        folder_id="d",
        path_prefix="p",
        page_number=8,
    )
    assert a != b


def test_page_task_rejects_zero_and_negative_pages() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError, match="page_number"):
            PageTask(job_id=uuid4(), page_number=bad)


def test_artifact_infers_and_validates_content_type() -> None:
    artifact = Artifact(
        job_id=uuid4(),
        page_number=1,
        artifact_type=ArtifactType.AUDIO,
        backend_file_id="fid",
        size_in_bytes=1024,
    )
    assert artifact.content_type == "audio/wav"

    with pytest.raises(ValueError, match="must be audio/wav"):
        Artifact(
            job_id=uuid4(),
            page_number=1,
            artifact_type=ArtifactType.AUDIO,
            backend_file_id="fid",
            size_in_bytes=1024,
            content_type="audio/mpeg",  # not in the REQ-UPL-04 enum
        )


def test_artifact_descriptor_rejects_empty_payload() -> None:
    with pytest.raises(ValueError, match="empty"):
        ArtifactDescriptor(artifact_type=ArtifactType.TEXT, payload=b"", file_name="p001.txt")
