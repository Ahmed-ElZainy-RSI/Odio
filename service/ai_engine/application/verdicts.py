"""Keep the verified tier's verdicts, and export them for review (spec 022 T019, 024 T005).

Saving verdicts must never fail a page: its artifacts are correct either way, and the
verdicts are the audit trail of how they got that way. So a store error is logged and
swallowed here -- deliberately, and only here, like usage metering.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ai_engine.domain.errors import PROGRAMMING_ERRORS
from ai_engine.domain.evidence import Evidence
from ai_engine.observability.logging import get_logger
from ai_engine.ports.verdicts import VerdictStore

log = get_logger(__name__)


class VerdictRecorder:
    def __init__(self, store: VerdictStore) -> None:
        self._store = store

    async def save(self, *, job_id: str, page: int, evidence: Sequence[Evidence]) -> None:
        try:
            await self._store.replace_page(job_id=job_id, page=page, evidence=evidence)
        except PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            log.warning(
                "verdicts.save_failed", page=page, verdicts=len(evidence), error=type(exc).__name__
            )


def _suspicion(evidence: Evidence) -> float:
    """The reviewer's suspicion score; 1.0 when nothing scored it (a person looks first)."""
    value = (evidence.scores or {}).get("suspicion")
    return float(value) if isinstance(value, (int, float)) else 1.0


def review_export(job_id: str, rows: Iterable[tuple[int, Evidence]]) -> dict[str, Any]:
    """The verdicts a person must look at, in the shape of the CLI's ``context_review.json``
    (``{"pages": {"<n>": [{id, ocr_word, proposed, suspicion, model, source}]}}``), so both
    pipelines' findings are reviewed the same way. Only REVIEW verdicts: an ACCEPT was
    applied with visual evidence, a REJECT changed nothing."""
    pages: dict[str, list[dict[str, Any]]] = {}
    for page, e in rows:
        if e.status != "REVIEW":
            continue
        pages.setdefault(str(page), []).append(
            {
                "id": e.token_id,
                "page": page,
                "ocr_word": e.original,
                "proposed": e.candidate,
                "suspicion": round(_suspicion(e), 4),
                "model": next((m for m in (e.models or {}).values() if m), None),
                "source": f"service-{e.detector}:{e.rule}",
            }
        )
    return {"job_id": job_id, "pages": pages}
