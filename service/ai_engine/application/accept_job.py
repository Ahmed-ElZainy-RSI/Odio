"""The AcceptJob use case (REQ-JOB-01/05, REQ-IDEM-01/02).

The only business logic reachable from an HTTP request. Everything expensive happens later
in a worker, which is why this can honestly answer 202 quickly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ai_engine.domain.enums import JobState, JobType, QualityTier
from ai_engine.domain.errors import AiEngineError, ConflictError, ExternalServiceError
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import JOBS_ACCEPTED, Metrics
from ai_engine.ports.job_store import JobStore

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AcceptJobCommand:
    """A validated, normalized inbound job request."""

    job_id: UUID
    job_type: JobType
    file_id: str
    folder_id: str
    path_prefix: str
    source_download_url: str
    page_number: int | None = None
    quality_tier: QualityTier = QualityTier.FAST


@dataclass(frozen=True, slots=True)
class AcceptJobResult:
    job_id: UUID
    state: JobState
    duplicate: bool


class AcceptJob:
    """Validate, deduplicate and persist one job."""

    def __init__(
        self,
        store: JobStore,
        *,
        metrics: Metrics,
        enable_verified_tier: bool = False,
        default_quality_tier: QualityTier = QualityTier.FAST,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._enable_verified_tier = enable_verified_tier
        self._default_quality_tier = default_quality_tier

    async def __call__(self, command: AcceptJobCommand) -> AcceptJobResult:
        tier = self._resolve_tier(command)

        job = Job(
            job_id=command.job_id,
            job_type=command.job_type,
            file_id=command.file_id,
            folder_id=command.folder_id,
            path_prefix=command.path_prefix,
            source_download_url=command.source_download_url,
            page_number=command.page_number,
            quality_tier=tier,
            payload_hash=canonical_payload_hash(
                job_type=command.job_type,
                file_id=command.file_id,
                folder_id=command.folder_id,
                path_prefix=command.path_prefix,
                page_number=command.page_number,
            ),
        )

        try:
            outcome = await self._store.accept(job)
        except AiEngineError:
            raise
        except Exception as exc:
            # The store is unreachable. Returning 202 here would be a lie: the Backend
            # would stop retrying a job that will never run, and the book would stall
            # silently and permanently. A 503 makes the Backend retry, which is correct.
            self._metrics.inc(
                JOBS_ACCEPTED,
                {"job_type": command.job_type.value, "outcome": "rejected"},
            )
            log.error(
                "job.accept_failed",
                job_id=str(command.job_id),
                detail=f"{type(exc).__name__}",
            )
            raise ExternalServiceError(
                "the job could not be persisted, so it was not accepted"
            ) from exc

        if outcome.conflict:
            self._metrics.inc(
                JOBS_ACCEPTED,
                {"job_type": command.job_type.value, "outcome": "rejected"},
            )
            log.warning(
                "job.rejected.conflict",
                job_id=str(command.job_id),
                job_type=command.job_type.value,
            )
            raise ConflictError(f"jobId {command.job_id} already exists with a different payload")

        self._metrics.inc(
            JOBS_ACCEPTED,
            {
                "job_type": command.job_type.value,
                "outcome": "new" if outcome.created else "duplicate",
            },
        )
        log.info(
            "job.accepted",
            job_id=str(command.job_id),
            job_type=command.job_type.value,
            quality_tier=tier.value,
            duplicate=not outcome.created,
        )
        return AcceptJobResult(
            job_id=outcome.job.job_id,
            state=outcome.job.state,
            duplicate=not outcome.created,
        )

    def _resolve_tier(self, command: AcceptJobCommand) -> QualityTier:
        """Apply the verified-tier kill-switch.

        A job asking for `verified` while the switch is off is DOWNGRADED rather than
        rejected, and the downgrade is logged. Rejecting would fail work the Backend
        cannot fix; silently honouring it would risk a 40-100x cost surprise.
        """
        requested = command.quality_tier or self._default_quality_tier
        if requested is QualityTier.VERIFIED and not self._enable_verified_tier:
            log.warning(
                "job.quality_tier.downgraded",
                job_id=str(command.job_id),
                requested=requested.value,
                applied=QualityTier.FAST.value,
                reason="ENABLE_VERIFIED_TIER is false",
            )
            return QualityTier.FAST
        return requested
