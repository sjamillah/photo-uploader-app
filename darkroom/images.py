"""Validate an upload and derive the two renditions the gallery serves."""

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS

# Passed to Image.open rather than only checked afterwards: Pillow otherwise
# tries every plugin it has in order to identify the bytes, so a file claiming
# to be a PSD reaches that decoder before any check of ours runs. Nearly every
# Pillow advisory is in a format nobody uploads on purpose.
# MPO, which is what iPhone burst and portrait shots are, has no entry of its
# own. The JPEG plugin hands off to it once it sees the MPO markers, so listing
# JPEG covers it and naming MPO here raises KeyError.
ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")


class InvalidImage(Exception):
    """Carries a message that is safe to show the person who uploaded."""


@dataclass(frozen=True)
class Rendition:
    display: bytes
    thumb: bytes
    width: int
    height: int


def process(stream) -> Rendition:
    try:
        probe = Image.open(stream, formats=ACCEPTED_FORMATS)
        probe.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImage(
            "We read JPEG, PNG, WebP and GIF. That file is either damaged or "
            "in some other format."
        ) from exc

    # verify() leaves the image object unusable, so open it again.
    stream.seek(0)
    image = Image.open(stream, formats=ACCEPTED_FORMATS)

    # Phones record rotation in EXIF instead of rotating pixels. Skip this and
    # portraits come out sideways.
    image = ImageOps.exif_transpose(image)
    image = _flatten(image)

    display = image.copy()
    display.thumbnail((config.DISPLAY_MAX, config.DISPLAY_MAX), Image.LANCZOS)

    thumb = image.copy()
    thumb.thumbnail((config.THUMB_MAX, config.THUMB_MAX), Image.LANCZOS)

    return Rendition(
        display=_encode(display, config.DISPLAY_QUALITY),
        thumb=_encode(thumb, config.THUMB_QUALITY),
        width=display.width,
        height=display.height,
    )


def _flatten(image: Image.Image) -> Image.Image:
    """Flatten transparency onto white and drop everything but pixels.

    The re-encode is also what strips EXIF, GPS tags included.
    """
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        return canvas
    return image.convert("RGB")


def _encode(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=4)
    return buffer.getvalue()
