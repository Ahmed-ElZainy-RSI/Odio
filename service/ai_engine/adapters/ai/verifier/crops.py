"""Cut one word out of the page image, in the variants the visual readers get (spec 022).

Boxes arrive as page percentages ``(x, y, w, h)`` -- the shape token boxes already have --
and are padded generously: an OCR line box subdivided by letter count is approximate, and a
crop that clips the word's first or last letter makes every reader wrong in the same way.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

#: pixels added around the word box (horizontal, vertical)
PADDING = (30, 20)
#: LightOn's parse endpoint rejects or downsamples anything larger
MAX_SIDE = 1540


def _image(page_image: bytes):
    from PIL import Image  # lazy: Pillow is in the `ai` extra

    img = Image.open(io.BytesIO(page_image))
    img.load()
    return img.convert("RGB")


def pixel_box(
    box: Sequence[float], width: int, height: int, padding: tuple[int, int] = PADDING
) -> tuple[int, int, int, int]:
    """Percent box -> padded, clamped pixel box ``(x1, y1, x2, y2)``."""
    x, y, w, h = (float(v) for v in box)
    pad_x, pad_y = padding
    x1 = max(0, int(x / 100 * width) - pad_x)
    y1 = max(0, int(y / 100 * height) - pad_y)
    x2 = min(width, int((x + w) / 100 * width) + pad_x)
    y2 = min(height, int((y + h) / 100 * height) + pad_y)
    if x2 - x1 < 4 or y2 - y1 < 4:
        raise ValueError(f"box {tuple(box)} is empty on a {width}x{height} page")
    return x1, y1, x2, y2


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fit(img, max_side: int = MAX_SIDE):
    longest = max(img.size)
    if longest <= max_side:
        return img
    scale = max_side / longest
    return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))


def word_variants(
    page_image: bytes, box: Sequence[float]
) -> tuple[dict[str, bytes], tuple[int, int, int, int]]:
    """PNG crops of one word: ``original``, ``upscaled`` (x3), ``contrast``, ``sharp``,
    ``threshold``. Returns them with the pixel box they were cut from."""
    from PIL import Image, ImageFilter, ImageOps

    page = _image(page_image)
    px = pixel_box(box, page.width, page.height)
    crop = page.crop(px)
    big = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(big)
    contrast = ImageOps.autocontrast(gray, cutoff=2)
    variants = {
        "original": crop,
        "upscaled": big,
        "contrast": contrast,
        "sharp": big.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3)),
        "threshold": contrast.point(lambda p: 255 if p > 128 else 0),
    }
    return {name: _png(_fit(img)) for name, img in variants.items()}, px


def upscale_for_reading(png: bytes, *, below: int = 700, target: int = 900) -> bytes:
    """A small crop read by a vision model is read badly: bring its longest side to ~900px."""
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    img.load()
    longest = max(img.size)
    if longest >= below:
        return png
    scale = target / longest
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    return _png(img)
