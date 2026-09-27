# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cover images: normalise with Pillow and store content-addressed.

Every cover (Sonos album art, share-link artwork, uploads) is converted to a
600×600 baseline JPEG without metadata. Nearly square images are cropped to
fill the square; wide or tall ones (e.g. radio logos) are padded instead, so
nothing important is cut off.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

COVER_SIZE = 600
MAX_PIXELS = 40_000_000
_NAME_RE = re.compile(r"^[0-9a-f]{20}\.jpg$")
_PAD_COLOUR = (240, 240, 240)
_FORMATS = ("JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF")


class CoverError(Exception):
    code = "upload_not_image"


def normalise(data: bytes) -> bytes:
    """Return ``data`` as a 600×600 baseline JPEG, or raise :class:`CoverError`."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data), formats=_FORMATS) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise CoverError("image has too many pixels")
                image.load()
                image = ImageOps.exif_transpose(image)
                image = _to_rgb(image)
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise CoverError(f"not a usable image: {exc}") from exc

    ratio = image.width / image.height
    if 0.9 <= ratio <= 1.1:
        image = ImageOps.fit(image, (COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)
    else:
        image = ImageOps.pad(
            image, (COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS, color=_PAD_COLOUR
        )
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85, progressive=False, optimize=True)
    return out.getvalue()


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


class CoverStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, data: bytes) -> str:
        """Normalise and store an image; return its file name."""
        jpeg = normalise(data)
        name = hashlib.sha256(jpeg).hexdigest()[:20] + ".jpg"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(path)
        return name

    def path(self, name: str) -> Path | None:
        """The file for a cover name, or None if the name is invalid or unknown."""
        if not _NAME_RE.match(name):
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def delete_unused(self, in_use: set[str]) -> int:
        """Remove stored covers that no tile uses; return how many."""
        removed = 0
        if not self.directory.is_dir():
            return 0
        for path in self.directory.iterdir():
            if _NAME_RE.match(path.name) and path.name not in in_use:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
