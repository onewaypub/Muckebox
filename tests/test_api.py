# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The kids API, pages, manifest and icons."""

import io
from html.parser import HTMLParser

import pytest
from PIL import Image

from muckebox.library import favorite_source
from muckebox.sonos.errors import SonosUnreachable

POST = {"X-Muckebox": "1"}


def add_tile(services, index=0, cover=None):
    from muckebox.sonos.fake import demo_favorites

    favorite = demo_favorites()[index]
    return services.library.add(
        favorite.title,
        favorite_source(favorite.item_id, favorite.ref, favorite.route, favorite.description),
        cover,
    )


def png(colour=(10, 200, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def connected(services):
    services.runtime.poll_transport()
    return services


# -- tiles and state ------------------------------------------------------------------


def test_tiles_in_display_order(client, services):
    cover = services.covers.save(png())
    first = add_tile(services, 0, cover)
    second = add_tile(services, 2)
    data = client.get("/api/tiles").get_json()
    assert data["rev"] == 2
    assert data["tiles"] == [
        {"id": first.id, "title": first.title, "cover": f"/covers/{cover}"},
        {"id": second.id, "title": second.title, "cover": None},
    ]


def test_state_supports_etag(client, connected):
    first = client.get("/api/state")
    assert first.status_code == 200
    assert first.get_json()["sonos"]["status"] == "ok"
    etag = first.headers["ETag"]
    assert first.headers["Cache-Control"] == "no-store"
    again = client.get("/api/state", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.data == b""
    tile = add_tile(connected)
    client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    changed = client.get("/api/state", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.get_json()["playback"]["tile_id"] == tile.id


def test_play_then_retap(client, connected):
    tile = add_tile(connected)
    first = client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    assert first.status_code == 202
    assert first.get_json()["result"] == "accepted"
    again = client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    assert again.status_code == 200
    assert again.get_json()["result"] == "noop"


def test_play_needs_csrf_header(client, connected):
    tile = add_tile(connected)
    assert client.post(f"/api/tiles/{tile.id}/play").status_code == 403


def test_play_unknown_tile(client, connected):
    response = client.post("/api/tiles/t-nope/play", headers=POST)
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "tile_not_found"


def test_play_while_busy(client, connected):
    tile = add_tile(connected)
    connected.runtime.state.update(pending={"action": "start", "tile_id": "x", "since": 0})
    response = client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "busy"


def test_unreachable_speaker(client, connected, fake_sonos):
    tile = add_tile(connected)
    fake_sonos.fail_next["playback"] = SonosUnreachable()
    connected.runtime.poll_transport()
    response = client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    assert response.status_code == 503
    assert response.get_json()["error"] == {"code": "sonos_unreachable", "retry_in": 2}
    assert client.get("/api/state").get_json()["sonos"]["status"] == "sonos_unreachable"


def test_not_configured(make_services):
    from muckebox.web import create_app

    services = make_services(room=None)
    client = create_app(services).test_client()
    assert client.get("/api/state").get_json()["sonos"]["status"] == "not_configured"
    tile = add_tile(services)
    response = client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    assert (response.status_code, response.get_json()["error"]["code"]) == (503, "not_configured")


def test_transport(client, connected):
    tile = add_tile(connected)
    client.post(f"/api/tiles/{tile.id}/play", headers=POST)
    response = client.post("/api/transport/toggle", headers=POST)
    assert response.status_code == 200
    assert response.get_json()["playback"]["state"] == "paused"
    assert client.post("/api/transport/eject", headers=POST).status_code == 404


def test_volume(client, connected, fake_sonos):
    fake_sonos.volume = 23
    response = client.post("/api/volume/up", headers=POST)
    assert response.get_json()["volume"] == {"value": 25, "max": 25, "limit": 25, "step": 3}
    assert client.post("/api/volume/sideways", headers=POST).status_code == 404
    fake_sonos.fail_next["get_volume"] = SonosUnreachable()
    failed = client.post("/api/volume/down", headers=POST)
    assert (failed.status_code, failed.get_json()["error"]["code"]) == (503, "volume_unknown")


# -- covers -----------------------------------------------------------------------------


def test_cover_is_served_immutable(client, services):
    name = services.covers.save(png())
    with client.get(f"/covers/{name}") as response:
        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"
        assert "max-age=31536000" in response.headers["Cache-Control"]


@pytest.mark.parametrize(
    "path", ["/covers/0123456789abcdef0123.jpg", "/covers/..%2Flibrary.json", "/covers/x.png"]
)
def test_unknown_or_invalid_cover(client, services, path):
    (services.settings.data_dir / "library.json").write_text("{}")
    assert client.get(path).status_code == 404


# -- pages ------------------------------------------------------------------------------


class _ScriptTags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and "src" not in attributes:
            self.inline.append(attributes)


def inline_scripts(html):
    parser = _ScriptTags()
    parser.feed(html)
    return parser.inline


def test_kids_page(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert '<html lang="de">' in html
    # The only inline script is the (non-executable) message catalogue.
    assert inline_scripts(html) == [{"id": "i18n", "type": "application/json"}]
    assert "Keine Verbindung zur Muckebox" in html


def test_admin_page(client):
    assert client.get("/admin").status_code == 200


def test_manifest(client):
    response = client.get("/manifest.webmanifest")
    assert response.mimetype == "application/manifest+json"
    data = response.get_json()
    assert data["start_url"] == "/"
    assert {icon["sizes"] for icon in data["icons"]} == {"192x192", "512x512"}


@pytest.mark.parametrize(("path", "size"), [("/apple-touch-icon.png", 180), ("/icon-512.png", 512)])
def test_icons(client, path, size):
    response = client.get(path)
    image = Image.open(io.BytesIO(response.data))
    assert image.size == (size, size)


def test_unknown_icon_size(client):
    assert client.get("/icon-99.png").status_code == 404


def test_state_reports_the_page_build(client, app):
    state = client.get("/api/state").get_json()
    assert state["assets"] == app.jinja_env.globals["asset_version"]
    assert f'data-asset-version="{state["assets"]}"' in client.get("/").get_data(as_text=True)


def test_volume_while_a_tap_is_running(client, connected, monkeypatch):
    from muckebox.runtime.service import Busy

    def busy(direction):
        raise Busy()

    monkeypatch.setattr(connected.runtime, "change_volume", busy)
    assert client.post("/api/volume/up", headers=POST).status_code == 409


def test_credits_of_the_game_assets(client):
    data = client.get("/api/credits").get_json()
    assert any(item["license"] == "CC BY-SA 3.0" for item in data["credits"])
    for path in ("/static/sounds/dog.mp3", "/static/pictures/dog.svg"):
        response = client.get(path)
        assert response.status_code == 200
        response.close()  # static files are streamed from an open file


def test_hammering_tiles_and_skip_is_refused_with_a_wait(client, connected):
    first, second = add_tile(connected, 0), add_tile(connected, 2)
    assert client.post(f"/api/tiles/{first.id}/play", headers=POST).status_code == 202
    response = client.post(f"/api/tiles/{second.id}/play", headers=POST)
    assert response.status_code == 409
    assert response.get_json()["error"] == {"code": "cooling_down", "retry_in": 5}
    assert client.post("/api/transport/next", headers=POST).status_code == 200
    response = client.post("/api/transport/next", headers=POST)
    assert response.get_json()["error"] == {"code": "cooling_down", "retry_in": 3}
    state = client.get("/api/state").get_json()
    assert state["cooldown"] == {"tile": 5, "skip": 3, "toggle": 1}
