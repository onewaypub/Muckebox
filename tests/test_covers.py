# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import io

import pytest
from PIL import Image

from muckebox.covers import COVER_SIZE, CoverError, CoverStore, normalise


def image_bytes(size=(300, 300), mode="RGB", fmt="PNG", colour=(200, 30, 30), **save):
    buffer = io.BytesIO()
    Image.new(mode, size, colour if mode != "RGBA" else (*colour, 0)).save(buffer, fmt, **save)
    return buffer.getvalue()


def open_jpeg(data):
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize(
    "data",
    [
        image_bytes(fmt="PNG"),
        image_bytes(fmt="WEBP"),
        image_bytes(fmt="GIF", mode="P", colour=3),
        image_bytes(mode="RGBA"),
        image_bytes(mode="CMYK", fmt="JPEG", colour=(0, 0, 0, 0)),
        image_bytes(size=(1200, 630), fmt="JPEG", progressive=True),
        image_bytes(size=(64, 1000)),
    ],
    ids=["png", "webp", "gif", "rgba", "cmyk", "wide-progressive", "tall"],
)
def test_everything_becomes_a_square_baseline_jpeg(data):
    image = open_jpeg(normalise(data))
    assert image.format == "JPEG"
    assert image.size == (COVER_SIZE, COVER_SIZE)
    assert image.mode == "RGB"
    assert not image.info.get("progressive")
    assert "exif" not in image.info


def test_transparent_areas_become_white():
    image = open_jpeg(normalise(image_bytes(mode="RGBA")))
    assert image.getpixel((300, 300)) == pytest.approx((255, 255, 255), abs=3)


def test_wide_logo_is_padded_not_cropped():
    wide = Image.new("RGB", (1000, 200), (0, 0, 255))
    buffer = io.BytesIO()
    wide.save(buffer, "PNG")
    image = open_jpeg(normalise(buffer.getvalue()))
    assert image.getpixel((300, 10)) != pytest.approx((0, 0, 255), abs=20)  # padding
    assert image.getpixel((300, 300)) == pytest.approx((0, 0, 255), abs=20)  # logo


def test_exif_orientation_is_applied():
    portrait = Image.new("RGB", (100, 300), (0, 200, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° when displayed
    buffer = io.BytesIO()
    portrait.save(buffer, "JPEG", exif=exif)
    image = open_jpeg(normalise(buffer.getvalue()))
    assert image.size == (COVER_SIZE, COVER_SIZE)


@pytest.mark.parametrize("data", [b"", b"not an image", b"%PDF-1.7 ...", b"<svg></svg>"])
def test_non_images_are_rejected(data):
    with pytest.raises(CoverError):
        normalise(data)


def test_decompression_bomb_is_rejected():
    buffer = io.BytesIO()
    Image.new("1", (10_000, 10_000)).save(buffer, "PNG")
    with pytest.raises(CoverError):
        normalise(buffer.getvalue())


def test_store_is_content_addressed(tmp_path):
    store = CoverStore(tmp_path / "covers")
    first = store.save(image_bytes())
    assert store.save(image_bytes()) == first
    assert store.path(first).is_file()
    other = store.save(image_bytes(colour=(0, 0, 200)))
    assert other != first
    assert store.delete_unused({first}) == 1
    assert store.path(other) is None
    assert store.path(first) is not None


@pytest.mark.parametrize("name", ["../library.json", "x.jpg", "0123456789abcdef0123.png", ""])
def test_invalid_cover_names(tmp_path, name):
    assert CoverStore(tmp_path).path(name) is None
