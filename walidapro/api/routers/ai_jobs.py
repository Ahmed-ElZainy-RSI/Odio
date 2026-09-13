from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..ai_contract import job_store, processing
from ..ai_contract.auth import verify_ai_engine_token

router = APIRouter(tags=["7. Backend <-> AI Engine contract"])

_KNOWN_JOB_TYPES = {
    "PAGES_IMG_AND_TEXT_EXTRACTION",
    "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION",
}


class JobEnqueueRequest(BaseModel):
    jobId: str
    jobType: str
    fileId: str
    folderId: str | None = None
    pathPrefix: str | None = None
    sourceDownloadUrl: str
    pageNumber: int | None = None  # required for PAGE_AUDIO_AND_TEXT_TIMING_GENERATION


@router.post(
    "/api/jobs",
    status_code=202,
    summary="Backend enqueues a job (matches the Backend<->AI-Engine spec exactly)",
    responses={
        202: {"description": "Accepted (new job queued, or an already-seen jobId acknowledged again)."},
        400: {"description": "Unknown jobType, or pageNumber missing for an audio/timing job."},
        401: {"description": "Missing/invalid/expired bearer token."},
    },
)
def enqueue_job(
    payload: JobEnqueueRequest,
    claims: dict = Depends(verify_ai_engine_token),
):
    """
    **Input:** JSON job envelope exactly as specified in the integration
    doc: `jobId`, `jobType`, `fileId`, `folderId`, `pathPrefix`,
    `sourceDownloadUrl`, and `pageNumber` (only for the audio/timing job).

    **Auth:** `Authorization: Bearer <ai-engine-bearer-token>` (Keycloak
    client_credentials, realm `reading-first`). Validated against the
    realm's JWKS; see `AI_ENGINE_AUTH_DISABLED` for local dev without a
    live Keycloak.

    **Output:** always `202 Accepted` (per spec) -- work happens in the
    background and results are pushed back to the backend via the
    `metadata` / `{page}/completed` / `{page}/failed` / `failed`
    callbacks, not in this response.

    **Idempotency:** a `jobId` already seen is acknowledged again with
    202 without being reprocessed.
    """
    if payload.jobType not in _KNOWN_JOB_TYPES:
        raise HTTPException(
            400, f"Unknown jobType '{payload.jobType}'. Expected one of {sorted(_KNOWN_JOB_TYPES)}."
        )

    if payload.jobType == "PAGE_AUDIO_AND_TEXT_TIMING_GENERATION" and payload.pageNumber is None:
        raise HTTPException(400, "pageNumber is required for PAGE_AUDIO_AND_TEXT_TIMING_GENERATION.")

    is_new = job_store.claim_job(payload.jobId, payload.jobType, payload.model_dump())

    if is_new:
        if payload.jobType == "PAGES_IMG_AND_TEXT_EXTRACTION":
            processing.submit(
                processing.process_pages_extraction_job,
                payload.jobId,
                payload.fileId,
                payload.folderId,
                payload.pathPrefix,
                payload.sourceDownloadUrl,
            )
        else:
            processing.submit(
                processing.process_audio_timing_job,
                payload.jobId,
                payload.fileId,
                payload.folderId,
                payload.pathPrefix,
                payload.sourceDownloadUrl,
                payload.pageNumber,
            )

    return {"jobId": payload.jobId, "status": "accepted", "duplicate": not is_new}


@router.get(
    "/api/jobs/{job_id}",
    summary="[Debug only, not in the spec] Inspect local processing state for a jobId",
)
def debug_get_job(job_id: str):
    """
    Not part of the backend contract -- the backend gets status via the
    callbacks this service sends it. This is only here so you can inspect
    what happened locally while there's no live backend to watch instead.
    """
    job = job_store.get_job(job_id)

    if job is None:
        raise HTTPException(404, "Unknown jobId.")

    return job
