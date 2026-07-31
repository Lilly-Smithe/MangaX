"""Shared Pillow limits for untrusted manga and cover images."""

from __future__ import annotations

from PIL import Image


MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 20_000

# Pillow performs its decompression-bomb check while opening an image, before
# callers can inspect dimensions. Keep that global guard aligned with MangaX's
# stricter per-image validation so crafted archives cannot allocate huge buffers.
Image.MAX_IMAGE_PIXELS = min(Image.MAX_IMAGE_PIXELS or MAX_IMAGE_PIXELS, MAX_IMAGE_PIXELS)


def validate_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("Görsel boyutu geçersiz.")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError("Görsel boyutları güvenli sınırı aşıyor.")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Görsel piksel sayısı güvenli sınırı aşıyor.")
