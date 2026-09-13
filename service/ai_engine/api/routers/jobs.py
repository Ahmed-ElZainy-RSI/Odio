"""`POST /api/jobs` -- the only functional endpoint in V1."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from ai_engine.api.deps import (
    authenticate,
    get_accept_job,
    get_settings_dep,
    get_store,
    validate_source_url,
)
from ai_engine.api.schemas import (
    ErrorResponse,
    JobAccepted,
    JobRequest,
    JobStatus,
    PageSummary,
)
from ai_engine.application.accept_job import AcceptJob, AcceptJobCommand
from ai_engine.config.settings import Settings
from ai_engine.domain.enums import PageState
from ai_engine.domain.errors import NotFoundError
from ai_engine.observability.context import bind
from ai_engine.ports.job_store import JobStore

router = APIRouter(prefix="/api", tags=["jobs"])

_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Body is not valid JSON"},
    401: {"model": ErrorResponse, "description": "Missing or unverifiable token"},
    403: {"model": ErrorResponse, "description": "Token from a client that is not allowed"},
    409: {"model": ErrorResponse, "description": "Same jobId, different payload"},
    422: {"model": ErrorResponse, "description": "Body failed field validation"},
    503: {"model": ErrorResponse, "description": "Job could not be persisted"},
}


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    response_model_by_alias=True,
    responses=_ERROR_RESPONSES,
    summary="Enqueue an extraction or audio-generation job",
)
async def create_job(
    body: JobRequest,
    request: Request,
    accept_job: Annotated[AcceptJob, Depends(get_accept_job)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    _caller: Annotated[str | None, Depends(authenticate)] = None,
) -> JobAccepted:
    """Accept a job and return `202` without doing any AI work.

    A duplicate `jobId` also returns `202` and does NOT start a second execution
    (REQ-IDEM-01). A duplicate `jobId` carrying a materially different payload returns
    `409` instead of silently dropping the new instruction.
    """
    with bind(job_id=str(body.job_id)):
        validate_source_url(body.source_download_url, settings)

        result = await accept_job(
            AcceptJobCommand(
                job_id=body.job_id,
                job_type=body.job_type,
                file_id=body.file_id,
                folder_id=body.folder_id,
                path_prefix=body.path_prefix,
                source_download_url=body.source_download_url,
                page_number=body.page_number,
                quality_tier=body.quality_tier or settings.default_quality_tier,
            )
        )
        return JobAccepted(
            job_id=result.job_id,
            status=result.state.value,
            duplicate=result.duplicate,
            accepted_at=datetime.now(UTC),
        )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    response_model_by_alias=True,
    responses={404: {"model": ErrorResponse}},
    summary="Inspect a job (operations only; not part of the V1 contract)",
)
async def get_job(
    job_id: UUID,
    store: Annotated[JobStore, Depends(get_store)],
    _caller: Annotated[str | None, Depends(authenticate)] = None,
) -> JobStatus:
    """Report which pages have and have not reported.

    PROPOSAL, optional for V1. It exists because an INVARIANT-1 failure is invisible from
    outside: when the Backend says "book X is stuck waiting for page 27", this answers it
    in one request instead of requiring database access.

    Returns no artifact bytes and no source URL.
    """
    job = await store.get(job_id)
    if job is None:
        raise NotFoundError(f"no job with id {job_id}")

    pages = await store.pages(job_id)
    non_terminal = sorted(p.page_number for p in pages if not p.is_terminal)
    return JobStatus(
        job_id=job.job_id,
        job_type=job.job_type,
        state=job.state.value,
        quality_tier=job.quality_tier,
        pages_count=job.pages_count,
        page_number=job.page_number,
        metadata_sent_at=job.metadata_sent_at,
        pages=PageSummary(
            reported_completed=sum(1 for p in pages if p.state is PageState.REPORTED_COMPLETED),
            reported_failed=sum(1 for p in pages if p.state is PageState.REPORTED_FAILED),
            running=sum(1 for p in pages if p.state is PageState.RUNNING),
            pending=sum(1 for p in pages if p.state is PageState.PENDING),
            non_terminal=non_terminal,
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
