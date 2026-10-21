"""Spec 020: the verifier contract, checked rather than trusted."""

from __future__ import annotations

from dataclasses import replace

from ai_engine.adapters.ai.verifier_passthrough import PassThroughVerifier
from ai_engine.domain.evidence import Evidence
from ai_engine.domain.tokens import Token
from ai_engine.domain.verification import Verdict, VerificationResult, contract_violations
from ai_engine.ports.ai import PageVerifier

BEFORE = [Token(id="p001w000", text="ذهب"), Token(id="p001w001", text="الفضل")]


def verdict(token_id: str, status: str = "ACCEPT") -> Verdict:
    return Verdict(
        Evidence(
            token_id=token_id,
            original="الفضل",
            candidate="الفصل",
            applied=status == "ACCEPT",
            status=status,  # type: ignore[arg-type]
            detector="verifier",
            decided_by="verifier",
            rule="decide()",
        )
    )


def corrected() -> list[Token]:
    return [BEFORE[0], replace(BEFORE[1], text="الفصل")]


def test_an_accepted_correction_is_clean() -> None:
    result = VerificationResult(tokens=corrected(), verdicts=[verdict("p001w001")])
    assert contract_violations(BEFORE, result) == []
    assert result.accepted == 1


def test_unchanged_page_with_no_verdicts_is_clean() -> None:
    assert contract_violations(BEFORE, VerificationResult(tokens=list(BEFORE))) == []


def test_text_change_without_accept_is_a_violation() -> None:
    result = VerificationResult(tokens=corrected(), verdicts=[verdict("p001w001", "REVIEW")])
    assert contract_violations(BEFORE, result) == ["p001w001 changed without an ACCEPT verdict"]
    assert result.needs_review == 1


def test_text_change_with_no_verdict_at_all_is_a_violation() -> None:
    assert contract_violations(BEFORE, VerificationResult(tokens=corrected())) == [
        "p001w001 changed without an ACCEPT verdict"
    ]


def test_dropping_a_token_is_a_violation() -> None:
    assert contract_violations(BEFORE, VerificationResult(tokens=BEFORE[:1])) == [
        "token count changed: 2 -> 1"
    ]


def test_renumbering_is_a_violation() -> None:
    renumbered = [replace(BEFORE[0], id="p001w009"), BEFORE[1]]
    assert contract_violations(BEFORE, VerificationResult(tokens=renumbered)) == [
        "token ids or their order changed"
    ]


def test_reordering_is_a_violation() -> None:
    assert contract_violations(BEFORE, VerificationResult(tokens=BEFORE[::-1])) == [
        "token ids or their order changed"
    ]


def test_verdict_for_a_token_not_on_the_page() -> None:
    result = VerificationResult(tokens=list(BEFORE), verdicts=[verdict("p002w000", "REVIEW")])
    assert contract_violations(BEFORE, result) == ["verdict for unknown token p002w000"]


def test_two_verdicts_for_one_token() -> None:
    result = VerificationResult(
        tokens=corrected(), verdicts=[verdict("p001w001"), verdict("p001w001")]
    )
    assert "more than one verdict for p001w001" in contract_violations(BEFORE, result)


def test_passthrough_implements_the_port_and_changes_nothing() -> None:
    verifier = PassThroughVerifier()
    assert isinstance(verifier, PageVerifier)
    result = verifier.verify(page_image=b"jpeg", tokens=BEFORE, page_number=1)
    assert list(result.tokens) == BEFORE
    assert result.verdicts == ()
    assert result.calls_made == {}
    assert contract_violations(BEFORE, result) == []
