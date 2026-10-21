"""Text-layer extraction: the free, exact path.

Wraps ``pipeline.extract`` from the legacy tree. For PDFs that carry an embedded text
layer -- 25 of the 36 sample books -- the text and per-word boxes come straight out of the
file, so this path costs **zero paid API calls** and introduces none of OCR's
letter-substitution errors.

The legacy function already emits exactly the record this service wants, including the
``p{page:03d}w{index:03d}`` id convention, so the adapter is a translation layer and
nothing more. Its four cleaning rules (kashida, leading punctuation, leading combining
mark, dropped hamza) are real Arabic domain knowledge and are reused untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ai_engine.adapters.ai.legacy import LegacyPipelineUnavailable, load_extract_module
from ai_engine.domain.errors import ModelUnavailableError, SourceInvalidError
from ai_engine.domain.tokens import PageProvenance, Token
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)


class TextLayerOcr:
    """Implements :class:`~ai_engine.ports.ai.OcrEngine` for born-digital PDFs."""

    name = "pdf-text-layer"
    # Free and local: never metered against a provider budget.
    provider = "local"
    model_id = "none"

    def __init__(self, *, legacy_repo_root: Path | None) -> None:
        self._legacy_repo_root = legacy_repo_root

    def extract_page(
        self, *, page_image: bytes | None, pdf_path: str | None, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]:
        if pdf_path is None:
            raise SourceInvalidError("the text-layer path needs the PDF, not a page image")

        try:
            extract = load_extract_module(self._legacy_repo_root)
        except LegacyPipelineUnavailable as exc:
            raise ModelUnavailableError(str(exc)) from exc

        import pymupdf

        with pymupdf.open(pdf_path) as doc:
            if not 1 <= page_number <= len(doc):
                raise SourceInvalidError(
                    f"page {page_number} is outside the document's 1..{len(doc)}"
                )
            records = extract.extract_page(doc[page_number - 1], page_number, page_number - 1)

        tokens = [
            Token(
                id=record["id"],
                text=record["txt"],
                raw=record.get("raw"),
                box=tuple(record["box"]) if record.get("box") else None,
                fixes=tuple(record.get("fixes") or ()),
            )
            for record in records
            # A token whose cleaned text is empty or contains whitespace would break the
            # Text artifact's space-join invariant. The legacy cleaner already drops empty
            # ones; this is belt and braces.
            if record.get("txt") and not any(c.isspace() for c in record["txt"])
        ]

        diac_ratio = extract.diacritic_ratio(" ".join(t.text for t in tokens))
        log.info(
            "ocr.extracted",
            stage="ocr",
            page_number=page_number,
            engine=self.name,
            tokens=len(tokens),
            diac_ratio=round(diac_ratio, 3),
        )
        # An illustration-only page legitimately yields zero tokens. That is a completable
        # page, not an error -- the Backend waits for every page in 1..N.
        return tokens, PageProvenance(
            ocr_engine=self.name,
            box_level="word",
            extra={"diac_ratio": f"{diac_ratio:.3f}", "source": "pdf-text-layer"},
        )
