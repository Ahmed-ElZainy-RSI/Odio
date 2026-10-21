"""A verifier that verifies nothing (spec 020).

It exists so the verified-tier plumbing -- tier routing, the contract check, metrics -- can
ship and be tested before a real verifier does (spec 022 ports walidapro's). With it wired,
a ``verified`` job produces exactly what a ``fast`` job does.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_engine.domain.tokens import Token
from ai_engine.domain.verification import VerificationResult


class PassThroughVerifier:
    """Implements :class:`~ai_engine.ports.ai.PageVerifier`."""

    name = "passthrough"

    def verify(
        self, *, page_image: bytes, tokens: Sequence[Token], page_number: int
    ) -> VerificationResult:
        return VerificationResult(tokens=list(tokens))
