"""AI capability ports.

These are specifications, not implementations: signature, contract and declared errors.
Adapters implement them; some wrap the existing `src/pipeline` code, and adapters are the
only layer permitted to import it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ai_engine.domain.tokens import (
    AudioFormat,
    BindReport,
    PageProvenance,
    TimedToken,
    Token,
)
from ai_engine.domain.verification import VerificationResult


@runtime_checkable
class PageRenderer(Protocol):
    """Rasterize PDF pages, one at a time."""

    def page_count(self, pdf_path: str) -> int:
        """Total pages in the document.

        Needed before any page work, because REQ-META-01 requires
        `PATCH /metadata {pagesCount:N}` first. Must be cheap -- the existing code already
        has `len(doc)` available and merely discards it.

        Raises:
            SourceInvalidError: the file is not a parseable PDF.
        """
        ...

    def render_page(self, pdf_path: str, page_number: int) -> bytes:
        """One page as JPEG bytes.

        MUST NOT materialize the whole document. The existing implementation returns every
        selected page as a PIL image in one list (~14 MB of RGB per page at 220 DPI), so a
        streaming variant is a prerequisite -- see T-3.1.
        """
        ...


@runtime_checkable
class OcrEngine(Protocol):
    """Turn one page into an ordered token sequence.

    Contract:
      - Tokens are in Arabic reading order (right-to-left within a line, top-to-bottom).
      - Ids are `p{page:03d}w{index:03d}` and are a pure function of (page, index).
      - `box` is present only where the engine supplies geometry. LightOn parse-v3 returns
        markdown with none at all.
      - An illustration-only page returns an EMPTY sequence, NOT an error. The Backend
        still waits for that page, so it must be reportable as completed.

    Raises:
        AiInferenceError: the provider failed or returned unusable output.
        ModelUnavailableError: the provider is down or over quota.
    Never raises SystemExit, and never swallows a provider outage into an empty result.
    """

    name: str
    model_id: str

    def extract_page(
        self, *, page_image: bytes | None, pdf_path: str | None, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]: ...


@runtime_checkable
class Diacritizer(Protocol):
    """Add Arabic diacritics (tashkeel) to a token sequence.

    HARD INVARIANT, inherited from the existing implementations and not negotiable: the
    consonantal skeleton must not change. For every token,
    ``strip_diacritics(out.text) == strip_diacritics(in.text)``. An implementation that
    cannot honour this for a token MUST leave that token unshaped rather than guess.

    Returns the tokens plus the fraction needing review, which the caller uses as a gate.
    """

    name: str
    model_id: str

    def diacritize(
        self, tokens: Sequence[Token], *, page_image: bytes | None, page_number: int
    ) -> tuple[Sequence[Token], float]: ...


@runtime_checkable
class TtsEngine(Protocol):
    """Synthesize one page of text into a single audio buffer.

    Contract:
      - Returns raw bytes plus an :class:`AudioFormat` describing them exactly.
      - `duration_ms` MUST be measured from the returned audio, never estimated from
        character count.
      - Exactly ONE buffer per call: the V1 callback carries a single Audio artifact.
    """

    name: str
    model_id: str

    def synthesize(
        self, text: str, *, voice: str | None = None
    ) -> tuple[bytes, AudioFormat, int]: ...


@runtime_checkable
class Aligner(Protocol):
    """Bind word-level timings onto stable token ids.

    Contract:
      - Every returned token either has a full window with a source, or no window at all.
        There is no third state.
      - Windows are monotonic and non-overlapping, and lie within [0, duration_ms].
      - A :class:`BindReport` is always returned. Zero anchors is a DEGRADED result, not
        an error -- the page still completes.
    """

    method: str

    def align(
        self,
        *,
        audio: bytes,
        audio_format: AudioFormat,
        duration_ms: int,
        tokens: Sequence[Token],
    ) -> tuple[Sequence[TimedToken], BindReport]: ...


@runtime_checkable
class PageVerifier(Protocol):
    """Verify an OCR'd page word by word against its image (the verified tier, spec 020).

    Contract (checked by the caller with ``domain.verification.contract_violations``, not
    trusted):
      - Token ids, their count and their order are unchanged.
      - Only a token with an ``ACCEPT`` verdict may change its ``text``; the verdict's
        evidence says why (spec 018).
      - Tokens the verifier did not flag get no verdict.
      - ``calls_made`` reports paid calls per provider, for budget and cost metering.

    Raises:
        AiInferenceError: a provider failed or returned unusable output.
        ModelUnavailableError: a provider is down or over quota.
    """

    name: str

    def verify(
        self, *, page_image: bytes, tokens: Sequence[Token], page_number: int
    ) -> VerificationResult: ...
