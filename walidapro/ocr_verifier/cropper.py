from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .config import SETTINGS


def padded_bbox(
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    mult: int = 1,
) -> tuple[int, int, int, int]:
    """
    توسيع الـ Bounding Box بمقدار padding.

    mult=1  -> word crop
    mult=4  -> context crop
    """

    x1, y1, x2, y2 = bbox
    w, h = size

    px = SETTINGS.padding_x * mult
    py = SETTINGS.padding_y * mult

    return (
        max(0, x1 - px),
        max(0, y1 - py),
        min(w, x2 + px),
        min(h, y2 + py),
    )


def _upscale(
    img: Image.Image,
    factor: int = 3,
) -> Image.Image:
    

    img = img.convert("RGB")

    return img.resize(
        (
            img.width * factor,
            img.height * factor,
        ),
        Image.Resampling.LANCZOS,
    )


def _grayscale(
    img: Image.Image,
) -> Image.Image:
   

    return ImageOps.grayscale(img)


def _increase_contrast(
    img: Image.Image,
    factor: float = 2.0,
) -> Image.Image:
    

    gray = _grayscale(img)

    enhancer = ImageEnhance.Contrast(
        gray
    )

    return enhancer.enhance(
        factor
    )


def _sharpen(
    img: Image.Image,
) -> Image.Image:
    

    upscaled = _upscale(
        img,
        factor=3,
    )

    gray = _grayscale(
        upscaled
    )

    return gray.filter(
        ImageFilter.UnsharpMask(
            radius=1.5,
            percent=180,
            threshold=2,
        )
    )


def _threshold(
    img: Image.Image,
) -> Image.Image:
   

    upscaled = _upscale(
        img,
        factor=3,
    )

    gray = _grayscale(
        upscaled
    )

    # Auto contrast أولاً
    gray = ImageOps.autocontrast(
        gray
    )

    # حساب threshold 
    pixels = list(
        gray.getdata()
    )

    if not pixels:
        return gray

    mean_value = (
        sum(pixels)
        / len(pixels)
    )

    
    threshold_value = int(
        max(
            80,
            min(
                210,
                mean_value * 0.90,
            ),
        )
    )

    bw = gray.point(
        lambda p: (
            255
            if p > threshold_value
            else 0
        )
    )

    return bw


def create_word_variants(
    word_crop: Image.Image,
) -> dict[str, Image.Image]:
    """
    إنشاء عدة نسخ من Crop الكلمة
    لتحسين القراءة البصرية.

    يرجع dict مثل:

    {
        "original": ...,
        "upscaled": ...,
        "contrast": ...,
        "sharp": ...,
        "threshold": ...
    }
    """

    original = word_crop.convert(
        "RGB"
    )

    variants = {
        "original": original,

        "upscaled": _upscale(
            original,
            factor=3,
        ),

        "contrast": _increase_contrast(
            _upscale(
                original,
                factor=3,
            ),
            factor=2.0,
        ),

        "sharp": _sharpen(
            original
        ),

        "threshold": _threshold(
            original
        ),
    }

    return variants


def save_crops(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
    out_dir: Path,
    index: int,
) -> tuple[Path, Path, dict[str, Path]]:
    """
    يحفظ:

    1. Word crop الأصلي
    2. Context crop
    3. نسخ محسنة من word crop

    Returns:

    word_path
    context_path
    variant_paths
    """

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    word_box = padded_bbox(
        bbox,
        image.size,
        mult=1,
    )

    context_box = padded_bbox(
        bbox,
        image.size,
        mult=4,
    )

    # ---------------------------------------------------------
    # Original crops
    # ---------------------------------------------------------

    word_crop = image.crop(
        word_box
    ).convert(
        "RGB"
    )

    context_crop = image.crop(
        context_box
    ).convert(
        "RGB"
    )

    word_path = (
        out_dir
        / f"{index:03d}_word.png"
    )

    context_path = (
        out_dir
        / f"{index:03d}_context.png"
    )

    word_crop.save(
        word_path
    )

    context_crop.save(
        context_path
    )

    # ---------------------------------------------------------
    # Image Processing Variants
    # ---------------------------------------------------------

    variants = create_word_variants(
        word_crop
    )

    variant_paths: dict[str, Path] = {}

    for name, variant_img in variants.items():

    
        if name == "original":
            variant_paths[
                name
            ] = word_path

            continue

        variant_path = (
            out_dir
            / f"{index:03d}_word_{name}.png"
        )

        variant_img.save(
            variant_path
        )

        variant_paths[
            name
        ] = variant_path

    return (
        word_path,
        context_path,
        variant_paths,
    )