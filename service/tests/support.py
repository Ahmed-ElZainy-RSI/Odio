"""Shared test helpers.

The important one is :func:`simulate_worker_crash`. Getting the crash model right matters
more than it looks: a page that has *reported* to the Backend is finished, and the stores
now refuse to un-terminate it. So "the worker died" must be modelled as pages left in a
non-terminal state -- which is exactly what a SIGKILL produces, because the process never
got as far as reporting anything.

Resetting a terminal page instead would be modelling something that cannot happen, and
would quietly assert that a double report is acceptable.
"""

from __future__ import annotations

from ai_engine.domain.enums import ArtifactType, JobState, PageState
from ai_engine.domain.job import Artifact, Job


async def simulate_worker_crash(store, job: Job) -> list[int]:
    """Model a SIGKILLed worker: requeue the job, reset only non-terminal pages.

    Returns the page numbers that were reset, so a caller can assert on them.

    A crash cannot un-report a page. Pages that already reached `REPORTED_COMPLETED` or
    `REPORTED_FAILED` had their final word with the Backend before the process died and are
    left alone; everything else goes back to `PENDING`, which is what lease expiry does.
    """
    await store.set_job_state(job.job_id, JobState.QUEUED)
    reset: list[int] = []
    for page in await store.pages(job.job_id):
        if page.is_terminal:
            continue
        await store.set_page_state(job.job_id, page.page_number, PageState.PENDING)
        reset.append(page.page_number)
    return reset


async def simulate_upload_then_crash(
    store,
    job: Job,
    page_number: int,
    *,
    artifacts: tuple[ArtifactType, ...],
    fake=None,
) -> None:
    """Model the worst window: artifacts confirmed, the report never sent.

    The page is left claimable, with its artifact ledger rows already present -- so a
    retried page must skip re-uploading them, and must still report exactly once. This is
    the scenario the outbox exists for, reproduced deliberately.
    """
    await store.ensure_page(job.job_id, page_number)
    for artifact_type in artifacts:
        file_id = f"pre-confirmed-{artifact_type.value.lower()}"
        await store.record_artifact(
            Artifact(
                job_id=job.job_id,
                page_number=page_number,
                artifact_type=artifact_type,
                backend_file_id=file_id,
                size_in_bytes=1234,
            )
        )
        if fake is not None:
            # The Backend also remembers the confirmed upload -- it would reject a
            # `completed` naming a file it never saw, and it is right to.
            fake.pre_confirm(file_id, content_type=artifact_type.content_type)
    await store.set_page_state(job.job_id, page_number, PageState.PENDING)
