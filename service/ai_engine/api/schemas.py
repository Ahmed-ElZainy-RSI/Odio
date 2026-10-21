"""Inbound and outbound HTTP schemas.

These are the ONLY pydantic models that cross the team boundary. They are deliberately
separate from the domain entities: the external contract must be able to stay still while
the domain is refactored, and internal structures must not leak outward.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_engine.domain.enums import JobType, QualityTier

_MAX_ID_LENGTH = 512


class JobRequest(BaseModel):
    """`POST /api/jobs` body. REQ-JOB-02 and REQ-JOB-03.

    Field names are camelCase because that is what the V1 spec specifies on the wire.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    job_id: UUID = Field(alias="jobId")
    job_type: JobType = Field(alias="jobType")
    file_id: str = Field(alias="fileId", min_length=1, max_length=128)
    folder_id: str = Field(alias="folderId", min_length=1, max_length=256)
    path_prefix: str = Field(alias="pathPrefix", min_length=1, max_length=_MAX_ID_LENGTH)
    source_download_url: str = Field(alias="sourceDownloadUrl", max_length=4096)
    page_number: int | None = Field(default=None, alias="pageNumber", ge=1)
    # PROPOSAL, not in the V1 PDF. Lets the tier be a per-job choice rather than a redeploy.
    quality_tier: QualityTier | None = Field(default=None, alias="qualityTier")

    @field_validator("folder_id", "path_prefix")
    @classmethod
    def _no_path_traversal(cls, value: str) -> str:
        """Reject traversal-shaped values.

        These are passed through to `initialize-upload`. They are never used to build a
        LOCAL path -- scratch paths derive from the job UUID alone -- but rejecting them
        here removes the possibility that a future change makes them dangerous.
        """
        if ".." in value or value.startswith(("/", "\\")) or "\x00" in value:
            raise ValueError("must not contain '..', a leading separator, or a NUL byte")
        return value

    @field_validator("source_download_url")
    @classmethod
    def _absolute_url(cls, value: str) -> str:
        """Structural URL check. The host allowlist is applied in the router.

        Host allowlisting needs configuration, so it cannot live in the model; but scheme
        and shape can be rejected here, at the earliest possible point.
        """
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("must be an absolute http(s) URL")
        if not parsed.hostname:
            raise ValueError("must include a host")
        return value

    @model_validator(mode="after")
    def _page_number_matches_job_type(self) -> JobRequest:
        """`pageNumber` is required for Job B and forbidden for Job A.

        Job A covers the whole document and discovers its own page count; a supplied page
        number there would be ambiguous rather than helpful.
        """
        if self.job_type.requires_page_number and self.page_number is None:
            raise ValueError(f"pageNumber is required for {self.job_type.value}")
        if not self.job_type.requires_page_number and self.page_number is not None:
            raise ValueError(f"pageNumber must not be supplied for {self.job_type.value}")
        return self


class JobAccepted(BaseModel):
    """`202` body.

    The V1 PDF specifies no response body. This is a PROPOSAL: a Backend engineer
    debugging a stuck book needs to know whether their POST created work or hit the
    dedupe path, and an empty 202 cannot tell them. The contract remains the status code.
    """

    job_id: UUID = Field(serialization_alias="jobId")
    status: str
    duplicate: bool
    accepted_at: datetime = Field(serialization_alias="acceptedAt")

    model_config = ConfigDict(populate_by_name=True)


class PageSummary(BaseModel):
    reported_completed: int = Field(serialization_alias="reportedCompleted")
    reported_failed: int = Field(serialization_alias="reportedFailed")
    running: int
    pending: int
    non_terminal: list[int] = Field(serialization_alias="nonTerminal")

    model_config = ConfigDict(populate_by_name=True)


class JobStatus(BaseModel):
    """`GET /api/jobs/{jobId}` body.

    PROPOSAL, explicitly optional for V1 and not part of the contract. It exists for one
    reason: an INVARIANT-1 failure is invisible from outside, and when the Backend reports
    "book X is stuck waiting for page 27" an engineer needs that answer in one request
    rather than via database access.
    """

    job_id: UUID = Field(serialization_alias="jobId")
    job_type: JobType = Field(serialization_alias="jobType")
    state: str
    quality_tier: QualityTier = Field(serialization_alias="qualityTier")
    pages_count: int | None = Field(serialization_alias="pagesCount")
    page_number: int | None = Field(serialization_alias="pageNumber")
    metadata_sent_at: datetime | None = Field(serialization_alias="metadataSentAt")
    pages: PageSummary
    created_at: datetime | None = Field(serialization_alias="createdAt")
    updated_at: datetime | None = Field(serialization_alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    request_id: str | None = Field(default=None, serialization_alias="requestId")

    model_config = ConfigDict(populate_by_name=True)


class ErrorResponse(BaseModel):
    """The single error envelope used by every non-2xx response."""

    error: ErrorBody


class DependencyCheck(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    role: str
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    role: str
    checks: dict[str, DependencyCheck]


class BudgetWindow(BaseModel):
    """One provider's allowance in the current window."""

    provider: str
    used: int
    remaining: int
    maxCalls: int
    windowStart: datetime
    windowReset: datetime


class BudgetResponse(BaseModel):
    windows: list[BudgetWindow]


class CostLine(BaseModel):
    provider: str
    model: str
    operation: str
    calls: int
    cached: int
    unpriced: int
    usd: float
    input_tokens: int
    output_tokens: int
    chars: int
    pages: int


class CostResponse(BaseModel):
    """Recorded paid-call usage, priced with the configured table (spec 025).

    `usd` is a lower bound whenever `unpriced_calls > 0`: those calls are counted, never
    priced at zero.
    """

    job_id: str | None = Field(default=None, serialization_alias="jobId")
    prices_as_of: str = Field(serialization_alias="pricesAsOf")
    calls: int
    usd: float
    unpriced_calls: int = Field(serialization_alias="unpricedCalls")
    lines: list[CostLine]


SourceUrl = Annotated[str, Field(max_length=4096)]
