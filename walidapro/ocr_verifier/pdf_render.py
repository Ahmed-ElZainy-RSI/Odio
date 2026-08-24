from __future__ import annotations

from pathlib import Path
from PIL import Image

try:
    import pypdfium2 as pdfium
except ImportError as exc:  
    raise RuntimeError(
        "Install pypdfium2: pip install pypdfium2"
    ) from exc


def parse_pages(
    spec: str | None,
    total_pages: int,
) -> list[int]:
    if not spec:
        return list(range(total_pages))

    out: list[int] = []

    for part in spec.split(","):
        part = part.strip()

        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(
                range(int(a) - 1, int(b))
            )
        else:
            out.append(int(part) - 1)

    return sorted({
        i
        for i in out
        if 0 <= i < total_pages
    })


def render_pdf(
    path: Path,
    dpi: int,
    pages: str | None = None,
) -> list[tuple[int, Image.Image]]:
    """
    Render selected PDF pages to independent PIL RGB images.

    The PdfDocument is always closed before returning so pypdfium2
    does not emit the "objects are still open" warning.
    """

    doc = pdfium.PdfDocument(str(path))

    try:
        indices = parse_pages(
            pages,
            len(doc),
        )

        scale = dpi / 72.0

        rendered: list[
            tuple[int, Image.Image]
        ] = []

        for i in indices:
            page = doc[i]

            try:
                bitmap = page.render(
                    scale=scale
                )

                try:
                    # .copy() detaches the returned image from the
                    # underlying PDF/bitmap resources before closing.
                    image = (
                        bitmap.to_pil()
                        .convert("RGB")
                        .copy()
                    )
                finally:
                    close_bitmap = getattr(
                        bitmap,
                        "close",
                        None,
                    )

                    if callable(close_bitmap):
                        close_bitmap()

            finally:
                close_page = getattr(
                    page,
                    "close",
                    None,
                )

                if callable(close_page):
                    close_page()

            rendered.append(
                (i + 1, image)
            )

        return rendered

    finally:
        doc.close()