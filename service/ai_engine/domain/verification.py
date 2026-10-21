"""The verified tier's result type and the contract every verifier must honour (spec 020).

A verifier checks an OCR'd page word by word against the page image and may correct
letters. Its mandate is narrow, and it is enforced here rather than trusted:

  - the token ids, their count and their order never change: timings, boxes and review
    rows all hang off those ids (constitution II);
  - only a token with an ``ACCEPT`` verdict may change its text;
  - every verdict names a token that exists on the page.

``contract_violations`` is pure, so the page task can check any verifier's output in one
line and fail the page (as ``INTERNAL_ERROR`` -- a verifier that breaks this is a bug)
instead of shipping corrupted anchoring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ai_engine.domain.errors import AiEngineError, ErrorCode
from ai_engine.domain.evidence import Evidence
from ai_engine.domain.tokens import Token


@dataclass(frozen=True, slots=True)
class Verdict:
    """The decision for one flagged token. Unflagged tokens get no verdict."""

    evidence: Evidence

    @property
    def token_id(self) -> str:
        return self.evidence.token_id

    @property
    def status(self) -> str:
        return self.evidence.status


@dataclass(frozen=True, slots=True)
class VerificationResult:
    tokens: Sequence[Token]
    verdicts: Sequence[Verdict] = ()
    #: paid calls this page cost, per provider -- feeds cost metering (spec 025)
    calls_made: Mapping[str, int] = field(default_factory=dict)

    @property
    def accepted(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "ACCEPT")

    @property
    def needs_review(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "REVIEW")


class VerifierContractViolation(AiEngineError):
    code = ErrorCode.INTERNAL_ERROR


def contract_violations(before: Sequence[Token], result: VerificationResult) -> list[str]:
    """Everything ``result`` did that a verifier is not allowed to do. Empty when clean."""
    after = result.tokens
    if [t.id for t in after] != [t.id for t in before]:
        if len(after) != len(before):
            return [f"token count changed: {len(before)} -> {len(after)}"]
        return ["token ids or their order changed"]

    problems: list[str] = []
    ids = {t.id for t in before}
    accepted = {v.token_id for v in result.verdicts if v.status == "ACCEPT"}
    seen: set[str] = set()
    for verdict in result.verdicts:
        if verdict.token_id not in ids:
            problems.append(f"verdict for unknown token {verdict.token_id}")
        if verdict.token_id in seen:
            problems.append(f"more than one verdict for {verdict.token_id}")
        seen.add(verdict.token_id)

    for old, new in zip(before, after, strict=True):
        if old.text != new.text and old.id not in accepted:
            problems.append(f"{old.id} changed without an ACCEPT verdict")
    return problems
