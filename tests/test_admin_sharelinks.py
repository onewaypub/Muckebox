# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adding share links on the parents' page, end to end with mocked services."""

import io
import re

import pytest
import requests_mock
from PIL import Image

from muckebox.netfetch import Fetcher
from muckebox.sonos.model import ShareLinkRef
from muckebox.web import auth, sharelinks

POST = {"X-Muckebox": "1"}
PUBLIC_V4 = "1.1.1.1"  # the fake resolver answers every host with this address
APPLE_ALBUM = "https://music.apple.com/de/album/beispiel-album/1440000001"


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 640), (30, 60, 200)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def offline_fetcher(monkeypatch):
    monkeypatch.setattr(
        sharelinks, "fetcher_factory", lambda: Fetcher(resolver=lambda host, port: [PUBLIC_V4])
    )
    monkeypatch.setattr("muckebox.web.admin._limiter", auth.RateLimiter())


@pytest.fixture
def web():
    with requests_mock.Mocker() as mocker:
        yield mocker


@pytest.fixture
def admin(client, services):
    services.runtime.poll_transport()
    client.post("/api/admin/login", json={"pin": "2468"}, headers=POST)
    return client


def add_link(admin, url):
    return admin.post("/api/admin/tiles", json={"source": "sharelink", "url": url}, headers=POST)


def test_apple_music_album_with_title_and_cover(admin, services, web):
    web.get(
        re.compile(r"https://music\.apple\.com/api/oembed\?url=.*"),
        json={
            "title": "Beispiel-Album",
            "thumbnail_url": "https://is1-ssl.mzstatic.com/image/thumb/Music/x/1200x630wp.jpg",
        },
    )
    web.get(
        re.compile(r"https://is1-ssl\.mzstatic\.com/.*"),
        content=png(),
        headers={"Content-Type": "image/png"},
    )
    response = add_link(admin, f"Hör mal: {APPLE_ALBUM} ")
    assert response.status_code == 201
    data = response.get_json()
    assert data["warnings"] == []
    tile = data["tile"]
    assert tile["title"] == "Beispiel-Album"
    assert tile["cover"].startswith("/covers/")
    assert tile["source"]["service"] == "apple_music"
    assert tile["source"]["kind"] == "album"

    # The kids can play it: Sonos gets the normalised link.
    kids_tile = services.library.get(tile["id"])
    admin.post(f"/api/tiles/{tile['id']}/play", headers=POST)
    link = ShareLinkRef("apple_music", "album", "1440000001")
    assert ("play_share_link", link, kids_tile.title) in services.runtime.backend.calls


def test_link_without_metadata_still_becomes_a_tile(admin, web):
    web.get(re.compile(r"https://open\.spotify\.com/.*"), status_code=500)
    response = add_link(admin, "https://open.spotify.com/album/0ExampleAlbum000000001?si=x")
    assert response.status_code == 201
    data = response.get_json()
    assert data["tile"]["title"] == "Spotify"
    assert data["tile"]["cover"] is None
    assert set(data["warnings"]) == {"title_missing", "cover_missing", "sharelink_experimental"}


def test_long_titles_are_shortened(admin, web):
    web.get(re.compile(r"https://music\.apple\.com/api/oembed\?url=.*"), json={"title": "x" * 90})
    web.get(re.compile(r"https://music\.apple\.com/de/.*"), status_code=404)
    tile = add_link(admin, APPLE_ALBUM).get_json()["tile"]
    assert len(tile["title"]) == 60
    assert tile["title"].endswith("…")


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/artist/0ExampleArtist00000001",
        "https://example.com/album/1",
        "no link at all",
    ],
)
def test_unsupported_links(admin, url):
    response = add_link(admin, url)
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        422,
        "sharelink_unsupported",
    )


def test_unresolvable_short_link(admin, web):
    web.get(re.compile(r"https://spotify\.link/.*"), status_code=404)
    web.head(re.compile(r"https://spotify\.link/.*"), status_code=404)
    response = add_link(admin, "https://spotify.link/AbCdEf")
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        422,
        "sharelink_unresolvable",
    )


@pytest.mark.parametrize("url", ["", "   ", "https://music.apple.com/" + "x" * 3000])
def test_invalid_input(admin, url):
    assert add_link(admin, url).status_code == 400


def test_share_links_need_login(client):
    assert add_link(client, APPLE_ALBUM).status_code == 401


def test_metadata_bugs_never_block_the_tile(admin, monkeypatch):
    from muckebox import linkmeta

    def broken(link, fetcher):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(linkmeta, "metadata", broken)
    response = add_link(admin, APPLE_ALBUM)
    assert response.status_code == 201
    assert "title_missing" in response.get_json()["warnings"]
