"""INVARIANT-1: Page Completion Totality.

The single most consequential requirement in the V1 contract:

    "the backend will consider that you finish your process after you completed the 30
     pages whether they fail or succeed, if 29, no, the backend will wait for number 30
     page."

A dropped page callback stalls a book forever, so these predicates get direct coverage.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ai_engine.domain.enums import PageState
from ai_engine.domain.invariants import audit_completion, pages_awaiting_report
from ai_engine.domain.job import PageTask

JOB = uuid4()


def page(number: int, state: PageState = PageState.PENDING) -> PageTask:
    return PageTask(job_id=JOB, page_number=number, state=state)


def test_all_pages_terminal_is_complete() -> None:
    pages = [
        page(1, PageState.REPORTED_COMPLETED),
        page(2, PageState.REPORTED_FAILED),
        page(3, PageState.REPORTED_COMPLETED),
    ]
    audit = audit_completion(3, pages)
    assert audit.is_complete
    assert audit.terminal == 3
    assert audit.non_terminal == ()


def test_a_failed_page_does_not_prevent_completion() -> None:
    """COMPLETED means every page *reported*, not every page *succeeded*.

    That is the spec's own semantics -- "whether they fail or succeed" -- and getting it
    wrong in either direction is a bug: failing the job would send a whole-job /failed the
    Backend does not expect.
    """
    pages = [page(1, PageState.REPORTED_FAILED), page(2, PageState.REPORTED_FAILED)]
    assert audit_completion(2, pages).is_complete


def test_one_missing_page_blocks_completion() -> None:
    """The 29-of-30 case, stated verbatim in the spec."""
    pages = [page(n, PageState.REPORTED_COMPLETED) for n in range(1, 30)]
    audit = audit_completion(30, pages)
    assert not audit.is_complete
    assert audit.missing == (30,)
    assert pages_awaiting_report(30, pages) == (30,)


def test_a_running_page_blocks_completion() -> None:
    pages = [page(1, PageState.REPORTED_COMPLETED), page(2, PageState.RUNNING)]
    audit = audit_completion(2, pages)
    assert not audit.is_complete
    assert audit.non_terminal == (2,)


def test_artifacts_uploaded_is_not_terminal() -> None:
    """Uploading is not reporting.

    This is precisely the window the callback outbox exists to close: the artifacts are in
    the Backend's storage but nothing has told the Backend so.
    """
    pages = [page(1, PageState.ARTIFACTS_UPLOADED)]
    audit = audit_completion(1, pages)
    assert not audit.is_complete
    assert audit.non_terminal == (1,)


def test_duplicate_ledger_row_is_malformed() -> None:
    pages = [page(1, PageState.REPORTED_COMPLETED), page(1, PageState.REPORTED_FAILED)]
    audit = audit_completion(1, pages)
    assert not audit.is_well_formed
    assert audit.duplicated == (1,)


def test_page_beyond_pages_count_is_malformed() -> None:
    pages = [page(1, PageState.REPORTED_COMPLETED), page(9, PageState.REPORTED_COMPLETED)]
    audit = audit_completion(1, pages)
    assert not audit.is_well_formed
    assert audit.unexpected == (9,)


def test_missing_and_running_pages_are_both_awaited() -> None:
    """From the Backend's side, "no row" and "still running" are the same: it is waiting."""
    pages = [page(1, PageState.REPORTED_COMPLETED), page(2, PageState.RUNNING)]
    assert pages_awaiting_report(4, pages) == (2, 3, 4)


def test_empty_ledger_with_zero_pages_is_complete() -> None:
    assert audit_completion(0, []).is_complete


def test_audit_accepts_a_generator() -> None:
    """The audit must not silently mis-count when handed a one-shot iterable."""
    pages = (page(n, PageState.REPORTED_COMPLETED) for n in (1, 2))
    audit = audit_completion(2, pages)
    assert audit.terminal == 2
    assert audit.is_complete


def test_negative_pages_count_rejected() -> None:
    with pytest.raises(ValueError, match="pages_count"):
        audit_completion(-1, [])


def test_describe_is_operator_readable() -> None:
    pages = [page(1, PageState.REPORTED_COMPLETED), page(2, PageState.RUNNING)]
    described = audit_completion(3, pages).describe()
    assert "1/3 pages terminal" in described
    assert "pending=[2]" in described
    assert "missing=[3]" in described
