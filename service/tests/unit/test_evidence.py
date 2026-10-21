"""Spec 018: the Evidence value object and its JSON contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_engine.domain.evidence import Evidence

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "specs/018-correction-evidence-record/contracts/evidence.schema.json"
PAGE_SCHEMA = REPO_ROOT / "src/pipeline/page-record.schema.json"


def accepted(**overrides) -> Evidence:
    values = {
        "token_id": "p007w042",
        "original": "الفضل",
        "candidate": "الفصل",
        "applied": True,
        "status": "ACCEPT",
        "detector": "verifier",
        "decided_by": "verifier",
        "rule": "decide()",
        "reads": {"lighton": "الفضل", "blind": "الفصل", "variants": ["الفصل", "الفصل"]},
        "scores": {"context": 0.93, "visual": 0.95, "final": 0.94},
        "bbox": (412, 880, 498, 915),
        "crop_ref": {"word": "crops/p007w042.png"},
        "models": {"blind": "gemini-3.6-flash"},
        "at": "2026-10-21T09:00:00+00:00",
    }
    values.update(overrides)
    return Evidence(**values)


def test_round_trip() -> None:
    evidence = accepted()
    assert Evidence.from_dict(evidence.to_dict()) == Evidence.from_dict(
        json.loads(json.dumps(evidence.to_dict()))
    )


def test_to_dict_matches_the_contract() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    jsonschema.validate(accepted().to_dict(), schema)
    minimal = Evidence(
        token_id="p001w000",
        original="a",
        candidate="b",
        applied=False,
        status="REVIEW",
        detector="ensemble",
        decided_by="rule",
        rule="third-answer",
    )
    jsonschema.validate(minimal.to_dict(), schema)
    assert "reads" not in minimal.to_dict()


def test_the_contract_equals_the_page_record_definition() -> None:
    """One definition of evidence: the standalone contract is the page-record $defs entry."""
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    page = json.loads(PAGE_SCHEMA.read_text(encoding="utf-8"))
    body = {k: v for k, v in contract.items() if k not in {"$schema", "$id", "title", "$defs"}}
    assert body == page["$defs"]["evidence"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "MAYBE"},
        {"detector": "gut-feeling"},
        {"decided_by": "nobody"},
        {"applied": True, "status": "REVIEW"},
        {"reads": {"oracle": "x"}},
    ],
)
def test_invalid_evidence_is_refused(overrides: dict) -> None:
    with pytest.raises(ValueError):
        accepted(**overrides)


def test_at_defaults_to_now_in_utc() -> None:
    evidence = Evidence(
        token_id="p001w000",
        original="a",
        candidate="b",
        applied=False,
        status="REJECT",
        detector="context",
        decided_by="human",
        rule="human-reject",
    )
    assert evidence.at.endswith("+00:00")
