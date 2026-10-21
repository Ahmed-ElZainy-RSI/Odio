"""PDF page rendering and page counting, via PyMuPDF.

Two things this does differently from the legacy code:

* **Streaming.** ``walidapro/ocr_verifier/pdf_render.py:41-107`` returns every selected
  page as a PIL image in one list -- roughly 14 MB of RGB per page at 220 DPI, so ~1.4 GB
  for a 100-page book before any work begins. Here a page is rendered on demand and its
  handles are released immediately.
* **`page_count` is a real return value.** The legacy drivers compute ``len(doc)`` and then
  only *print* it (``src/pipeline/run_ocr_gemini.py:38-40``), which is why nothing could
  satisfy the spec's "give the number of pages before you start" requirement.

Render parameters match the existing pipeline so artifacts stay comparable with the 119
real JPEGs already on disk: 2.0x matrix, thumbnailed to a 1100 px long edge, JPEG q82.
"""

from __future__ import annotations

import io
from pathlib import Path

from ai_engine.domain.errors import SourceInvalidError
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)

RENDER_SCALE = 2.0
MAX_EDGE = 1100
JPEG_QUALITY = 82


class PyMuPdfRenderer:
    """Implements :class:`~ai_engine.ports.ai.PageRenderer`."""

    name = "pymupdf"

    def __init__(self, *, scale: float = RENDER_SCALE, max_edge: int = MAX_EDGE) -> None:
        self._scale = scale
        self._max_edge = max_edge

    def page_count(self, pdf_path: str) -> int:
        import pymupdf  # imported lazily: adapters may load heavy deps, the domain may not

        try:
            with pymupdf.open(pdf_path) as doc:
                return len(doc)
        except SourceInvalidError:
            raise
        except Exception as exc:
            # A malformed PDF is untrusted input handed to a native library. It becomes
            # data (`SOURCE_INVALID`), never a worker crash.
            raise SourceInvalidError(f"could not open the PDF: {type(exc).__name__}") from exc

    def has_text_layer(self, pdf_path: str, *, min_words: int = 50) -> bool:
        """Whether the document carries a usable embedded text layer.

        Mirrors ``src/pipeline/main.py:53``. When true, extraction is exact and costs
        **zero** paid API calls -- 25 of the 36 sample books qualify.
        """
        import pymupdf

        with pymupdf.open(pdf_path) as doc:
            words = sum(len(page.get_text("words")) for page in doc)
        return words >= min_words

    def render_page(self, pdf_path: str, page_number: int) -> bytes:
        """One page as JPEG bytes. 1-based page numbering throughout."""
        import pymupdf
        from PIL import Image

        with pymupdf.open(pdf_path) as doc:
            if not 1 <= page_number <= len(doc):
                raise SourceInvalidError(
                    f"page {page_number} is outside the document's 1..{len(doc)}"
                )
            page = doc[page_number - 1]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(self._scale, self._scale))
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

        image.thumbnail((self._max_edge, self._max_edge * 3), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        payload = buffer.getvalue()
        log.info(
            "page.rendered",
            stage="render",
            page_number=page_number,
            size_in_bytes=len(payload),
            width=image.width,
            height=image.height,
        )
        return payload

    @staticmethod
    def verify(pdf_path: str) -> None:
        """Confirm the file is a PDF before anything expensive touches it."""
        head = Path(pdf_path).read_bytes()[:8]
        if not head.startswith(b"%PDF-"):
            raise SourceInvalidError(f"not a PDF (magic bytes {head!r})")
