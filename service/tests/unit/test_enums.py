"""Wire-facing enum values are part of the contract."""

from __future__ import annotations

from ai_engine.domain.enums import (
    ALLOWED_CONTENT_TYPES,
    ARTIFACT_ORDER,
    ArtifactType,
    JobState,
    JobType,
    PageState,
)


def test_artifact_type_wire_values_are_exact() -> None:
    """REQ-CB-02/03: capitalised exactly as the Backend expects."""
    assert [a.value for a in ArtifactType] == ["Image", "Text", "Audio", "JSON"]


def test_content_types_are_the_four_permitted_ones() -> None:
    """REQ-UPL-04: no other content type may ever be sent."""
    permitted = {"image/jpeg", "text/plain", "audio/wav", "application/json"}
    assert ALLOWED_CONTENT_TYPES == permitted
    assert ArtifactType.AUDIO.content_type == "audio/wav"
    assert ArtifactType.JSON.content_type == "application/json"


def test_upload_order_is_mandated_per_job_type() -> None:
    """REQ-UPL-05: image then text; audio then json."""
    assert ARTIFACT_ORDER[JobType.PAGES_IMG_AND_TEXT_EXTRACTION] == (
        ArtifactType.IMAGE,
        ArtifactType.TEXT,
    )
    assert ARTIFACT_ORDER[JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION] == (
        ArtifactType.AUDIO,
        ArtifactType.JSON,
    )


def test_job_type_page_number_requirement() -> None:
    assert JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION.requires_page_number
    assert not JobType.PAGES_IMG_AND_TEXT_EXTRACTION.requires_page_number


def test_terminal_state_sets() -> None:
    assert JobState.COMPLETED.is_terminal
    assert JobState.FAILED.is_terminal
    assert JobState.DEAD_LETTER.is_terminal
    assert not JobState.QUEUED.is_terminal
    assert not JobState.RUNNING.is_terminal

    assert PageState.REPORTED_COMPLETED.is_terminal
    assert PageState.REPORTED_FAILED.is_terminal
    # Uploading is not reporting: this is the window the outbox closes.
    assert not PageState.ARTIFACTS_UPLOADED.is_terminal
    assert not PageState.PENDING.is_terminal
    assert not PageState.RUNNING.is_terminal
