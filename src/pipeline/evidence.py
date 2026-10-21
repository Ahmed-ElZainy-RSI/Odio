"""Correction evidence: why one token's text was, or was not, changed (spec 018).

A correction that cannot be explained after the fact cannot be audited or challenged.
This is the one shape every detector writes, CLI tools and the verified tier alike: what
flagged the token, every independent reading of the word, the scores, the rule or person
that authorised the change, and when. It mirrors walidapro's ``SuspiciousWord`` field for
field, so the two pipelines' audit logs compare like with like.

The JSON form is ``specs/018-correction-evidence-record/contracts/evidence.schema.json``
(``$defs.evidence`` in the page-record schema). Stdlib only, like the rest of ``domain/``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Status = Literal["ACCEPT", "REVIEW", "REJECT"]
Detector = Literal["ensemble", "context", "reviewer", "verifier"]
DecidedBy = Literal["rule", "human", "verifier"]

_STATUSES = frozenset({"ACCEPT", "REVIEW", "REJECT"})
_DETECTORS = frozenset({"ensemble", "context", "reviewer", "verifier"})
_DECIDERS = frozenset({"rule", "human", "verifier"})
_READ_KEYS = frozenset({"lighton", "gemini", "vlm", "llm", "blind", "paddle", "variants"})


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Evidence:
    token_id: str
    original: str
    candidate: str
    applied: bool
    status: Status
    detector: Detector
    decided_by: DecidedBy
    rule: str
    at: str = field(default_factory=utc_now)
    reads: Mapping[str, Any] = field(default_factory=dict)
    scores: Mapping[str, float | None] = field(default_factory=dict)
    bbox: Sequence[float] | None = None
    crop_ref: Mapping[str, str] | None = None
    models: Mapping[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"status must be one of {sorted(_STATUSES)}, got {self.status!r}")
        if self.detector not in _DETECTORS:
            raise ValueError(f"unknown detector {self.detector!r}")
        if self.decided_by not in _DECIDERS:
            raise ValueError(f"unknown decided_by {self.decided_by!r}")
        if self.applied and self.status != "ACCEPT":
            raise ValueError("only an ACCEPT can be applied")
        unknown = set(self.reads) - _READ_KEYS
        if unknown:
            raise ValueError(f"unknown reads {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready; empty optional groups are omitted."""
        out: dict[str, Any] = {
            "token_id": self.token_id,
            "original": self.original,
            "candidate": self.candidate,
            "applied": self.applied,
            "status": self.status,
            "detector": self.detector,
            "decided_by": self.decided_by,
            "rule": self.rule,
            "at": self.at,
        }
        if self.reads:
            out["reads"] = {k: list(v) if k == "variants" else v for k, v in self.reads.items()}
        if self.scores:
            out["scores"] = dict(self.scores)
        if self.bbox is not None:
            out["bbox"] = list(self.bbox)
        if self.crop_ref is not None:
            out["crop_ref"] = dict(self.crop_ref)
        if self.models:
            out["models"] = dict(self.models)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Evidence:
        return cls(
            token_id=data["token_id"],
            original=data["original"],
            candidate=data["candidate"],
            applied=data["applied"],
            status=data["status"],
            detector=data["detector"],
            decided_by=data["decided_by"],
            rule=data["rule"],
            at=data["at"],
            reads=data.get("reads", {}),
            scores=data.get("scores", {}),
            bbox=data.get("bbox"),
            crop_ref=data.get("crop_ref"),
            models=data.get("models", {}),
        )
