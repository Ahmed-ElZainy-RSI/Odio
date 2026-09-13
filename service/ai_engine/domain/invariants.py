"""INVARIANT-1: Page Completion Totality.

    For a job with `pagesCount = N`, the engine must deliver exactly one terminal
    callback -- /completed or /failed -- for every page in 1..N, or escalate the whole
    job via PATCH /api/jobs/{jobId}/failed.

The V1 spec is unambiguous about the consequence of failing this: "the backend will
consider that you finish your process after you completed the 30 pages whether they fail
or succeed, if 29, no, the backend will wait for number 30 page."

A dropped page callback stalls a book in the Backend forever, so these predicates are
pure, stdlib-only and directly unit-tested.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_engine.domain.job import PageTask


@dataclass(frozen=True, slots=True)
class CompletionAudit:
    """The answer to 'is this job safe to mark COMPLETED?'"""

    expected: int
    terminal: int
    non_terminal: tuple[int, ...]
    missing: tuple[int, ...]
    unexpected: tuple[int, ...]
    duplicated: tuple[int, ...]

    @property
    def is_complete(self) -> bool:
        """True only when every expected page has exactly one terminal report."""
        return self.is_well_formed and not self.non_terminal

    @property
    def is_well_formed(self) -> bool:
        """True when the ledger covers exactly pages 1..expected, once each.

        A malformed ledger is a bug on our side, and is distinct from work still in
        progress -- hence a separate predicate from :attr:`is_complete`.
        """
        return not self.missing and not self.unexpected and not self.duplicated

    def describe(self) -> str:
        """Operator-facing summary, used in reconciler logs."""
        parts = [f"{self.terminal}/{self.expected} pages terminal"]
        if self.non_terminal:
            parts.append(f"pending={list(self.non_terminal)}")
        if self.missing:
            parts.append(f"missing={list(self.missing)}")
        if self.unexpected:
            parts.append(f"unexpected={list(self.unexpected)}")
        if self.duplicated:
            parts.append(f"duplicated={list(self.duplicated)}")
        return ", ".join(parts)


def audit_pages(expected_pages: Iterable[int], pages: Iterable[PageTask]) -> CompletionAudit:
    """Compare a page ledger against the exact set of pages the Backend is waiting for.

    Taking an explicit page *set* rather than a count is what makes this work for both job
    types. Job A waits for ``1..pagesCount``; Job B waits for exactly one supplied page,
    which may be page 7 of a book. A count-based audit would call that page "unexpected"
    and pages 1-6 "missing", which is nonsense.

    Accepts any iterable and materializes each once, so generator arguments are safe.
    """
    expected = set(expected_pages)
    if any(n < 1 for n in expected):
        raise ValueError("expected page numbers must be >= 1")

    ledger = list(pages)
    counts: dict[int, int] = {}
    for page in ledger:
        counts[page.page_number] = counts.get(page.page_number, 0) + 1

    return CompletionAudit(
        expected=len(expected),
        terminal=sum(1 for p in ledger if p.is_terminal),
        non_terminal=tuple(sorted({p.page_number for p in ledger if not p.is_terminal})),
        missing=tuple(sorted(n for n in expected if n not in counts)),
        unexpected=tuple(sorted(n for n in counts if n not in expected)),
        duplicated=tuple(sorted(n for n, c in counts.items() if c > 1)),
    )


def audit_completion(pages_count: int, pages: Iterable[PageTask]) -> CompletionAudit:
    """Job A form: the Backend is waiting for pages ``1..pages_count``."""
    if pages_count < 0:
        raise ValueError(f"pages_count must be >= 0, got {pages_count}")
    return audit_pages(range(1, pages_count + 1), pages)


def pages_awaiting_report(pages_count: int, pages: Iterable[PageTask]) -> tuple[int, ...]:
    """Page numbers the Backend is still waiting on.

    Includes pages that are merely missing from the ledger, because from the Backend's
    point of view "we never created a row for page 27" and "page 27 is still running" are
    the same thing: it is waiting.
    """
    audit = audit_completion(pages_count, pages)
    return tuple(sorted(set(audit.non_terminal) | set(audit.missing)))
