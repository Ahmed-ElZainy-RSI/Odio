"""Where the verified tier's verdicts are kept (spec 022 T019)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_engine.domain.evidence import Evidence


@runtime_checkable
class VerdictStore(Protocol):
    """Each page's verdicts, with their evidence, readable per job.

    ``replace_page`` replaces whatever the page had: a retried page is verified again, and
    its new verdicts are the ones that describe what was shipped.
    """

    async def replace_page(
        self, *, job_id: str, page: int, evidence: Sequence[Evidence]
    ) -> None: ...

    async def for_job(
        self, job_id: str, *, page: int | None = None
    ) -> list[tuple[int, Evidence]]: ...
