"""Domain enumerations.

Wire-facing values are EXACT and must not be changed casually: `ArtifactType` and the
`jobType` strings are part of the V1 contract with the Backend team.

See docs/backend-v1/10-data-contracts.md section 4.
"""

from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    """REQ-JOB-02 / REQ-JOB-03. Exact wire values."""

    PAGES_IMG_AND_TEXT_EXTRACTION = "PAGES_IMG_AND_TEXT_EXTRACTION"
    PAGE_AUDIO_AND_TEXT_TIMING_GENERATION = "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION"

    @property
    def requires_page_number(self) -> bool:
        """Job B operates on exactly one supplied page; Job A covers the whole document."""
        return self is JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION


class JobState(StrEnum):
    """Job lifecycle. See docs/backend-v1/09-async-jobs.md section 2.

    NOTE: COMPLETED means *every page reported*, not *every page succeeded*. That is
    REQ-META-02's own semantics: "whether they fail or succeed".
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_JOB_STATES


_TERMINAL_JOB_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.DEAD_LETTER})


class PageState(StrEnum):
    """Page task lifecycle. Exactly two terminal states, and every page must reach one
    of them exactly once -- that sentence is INVARIANT-1."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    ARTIFACTS_UPLOADED = "ARTIFACTS_UPLOADED"
    REPORTED_COMPLETED = "REPORTED_COMPLETED"
    REPORTED_FAILED = "REPORTED_FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_PAGE_STATES


_TERMINAL_PAGE_STATES = frozenset({PageState.REPORTED_COMPLETED, PageState.REPORTED_FAILED})


class ArtifactType(StrEnum):
    """REQ-CB-02 / REQ-CB-03. Capitalised exactly as the Backend expects."""

    IMAGE = "Image"
    TEXT = "Text"
    AUDIO = "Audio"
    JSON = "JSON"

    @property
    def content_type(self) -> str:
        """REQ-UPL-04: only these four content types may ever be sent."""
        return _CONTENT_TYPES[self]


_CONTENT_TYPES: dict[ArtifactType, str] = {
    ArtifactType.IMAGE: "image/jpeg",
    ArtifactType.TEXT: "text/plain",
    ArtifactType.AUDIO: "audio/wav",
    ArtifactType.JSON: "application/json",
}

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(_CONTENT_TYPES.values())

#: Upload order is mandated by REQ-UPL-05: image then text, audio then json.
ARTIFACT_ORDER: dict[JobType, tuple[ArtifactType, ArtifactType]] = {
    JobType.PAGES_IMG_AND_TEXT_EXTRACTION: (ArtifactType.IMAGE, ArtifactType.TEXT),
    JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION: (ArtifactType.AUDIO, ArtifactType.JSON),
}


class QualityTier(StrEnum):
    """PROPOSAL, not in the V1 PDF. Selects how much a page's OCR is checked.

    `FAST`     -> OCR as-is (1-2 paid calls/page)
    `VERIFIED` -> OCR, then the visual verifier (spec 022): every suspect word is re-read
                  from the page image before its letters may change (up to ~9 paid calls
                  per suspect, capped per page by VERIFIER_MAX_SUSPECTS)

    See docs/backend-v1/19-adrs.md ADR-009.
    """

    FAST = "fast"
    VERIFIED = "verified"


class TimingSource(StrEnum):
    """Provenance of a single token's timing.

    `ANCHOR`       -> matched a real speech timestamp
    `INTERPOLATED` -> filled proportionally between anchors by letter count
    """

    ANCHOR = "anchor"
    INTERPOLATED = "interpolated"


class CallbackKind(StrEnum):
    """The four outbound progress calls of the V1 contract."""

    METADATA = "metadata"
    PAGE_COMPLETED = "page_completed"
    PAGE_FAILED = "page_failed"
    JOB_FAILED = "job_failed"
