# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app icon, drawn with Pillow (no binary files in the repository)."""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw

BACKGROUND = (29, 53, 87)
NOTE = (255, 209, 102)
SIZES = (180, 192, 512)


@lru_cache(maxsize=len(SIZES))
def app_icon(size: int) -> bytes:
    """A music note on a blue square, as PNG. Safe for maskable icons."""
    scale = 4  # draw large, then downsample for smooth edges
    s = size * scale
    image = Image.new("RGB", (s, s), BACKGROUND)
    draw = ImageDraw.Draw(image)

    def box(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s))

    # Two note heads, two stems and a beam, all within the maskable safe zone.
    draw.ellipse(box(0.26, 0.60, 0.44, 0.74), fill=NOTE)
    draw.ellipse(box(0.56, 0.54, 0.74, 0.68), fill=NOTE)
    draw.rectangle(box(0.40, 0.30, 0.44, 0.67), fill=NOTE)
    draw.rectangle(box(0.70, 0.24, 0.74, 0.61), fill=NOTE)
    draw.polygon(
        [(0.40 * s, 0.30 * s), (0.74 * s, 0.24 * s), (0.74 * s, 0.33 * s), (0.40 * s, 0.39 * s)],
        fill=NOTE,
    )
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
