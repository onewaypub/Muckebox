# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The parents' page API, including its security properties."""

import io

import pytest
from PIL import Image

from muckebox.web import auth
from muckebox.web.app import MAX_UPLOAD_BYTES, create_app

POST = {"X-Muckebox": "1"}


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch):
    monkeypatch.setattr("muckebox.web.admin._limiter", auth.RateLimiter())


def login(client, pin="2468", **kwargs):
    return client.post("/api/admin/login", json={"pin": pin}, headers=POST, **kwargs)


@pytest.fixture
def admin(client, services):
    services.runtime.poll_transport()
    assert login(client).status_code == 200
    return client


def png(size=(300, 300), colour=(200, 10, 10)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def add_favorite(admin, index):
    item_id = f"FV:2/{index}"
    return admin.post(
        "/api/admin/tiles", json={"source": "favorite", "item_id": item_id}, headers=POST
    )


# -- locked, login, session --------------------------------------------------------------


def test_locked_without_pin(make_services):
    client = create_app(make_services(ADMIN_PIN="")).test_client()
    assert client.get("/api/admin/session").get_json() == {
        "ok": True,
        "locked": True,
        "logged_in": False,
    }
    response = login(client, "")
    assert (response.status_code, response.get_json()["error"]["code"]) == (403, "admin_locked")
    assert client.get("/api/admin/tiles").status_code == 403


def test_short_pin_locks_too(make_services):
    client = create_app(make_services(ADMIN_PIN="12")).test_client()
    assert client.get("/api/admin/session").get_json()["locked"] is True


@pytest.mark.parametrize("path", ["/api/admin/status", "/api/admin/tiles", "/api/admin/favorites"])
def test_endpoints_need_login(client, path):
    response = client.get(path)
    assert (response.status_code, response.get_json()["error"]["code"]) == (401, "login_required")


def test_wrong_pin(client):
    response = login(client, "0000")
    assert (response.status_code, response.get_json()["error"]["code"]) == (401, "pin_wrong")


def test_login_sets_a_strict_http_only_cookie(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert client.get("/api/admin/session").get_json()["logged_in"] is True


def test_rate_limit_per_client(client):
    for _ in range(5):
        assert login(client, "0000").status_code == 401
    blocked = login(client)  # even the right PIN is refused now
    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "pin_rate_limited"
    assert blocked.get_json()["error"]["retry_in"] > 0
    other = login(client, environ_base={"REMOTE_ADDR": "192.0.2.77"})
    assert other.status_code == 200


def test_global_rate_limit():
    limiter = auth.RateLimiter(clock=lambda: 0.0)
    for number in range(auth.MAX_FAILURES_GLOBAL):
        limiter.failure(f"192.0.2.{number}")
    assert limiter.retry_in("198.51.100.1") is not None


def test_rate_limit_expires():
    now = {"t": 0.0}
    limiter = auth.RateLimiter(clock=lambda: now["t"])
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        limiter.failure("192.0.2.5")
    assert limiter.retry_in("192.0.2.5") is not None
    now["t"] = auth.FAILURE_WINDOW + 1
    assert limiter.retry_in("192.0.2.5") is None


def test_changing_the_pin_ends_sessions(make_services, services):
    app = create_app(services)
    client = app.test_client()
    login(client)
    assert client.get("/api/admin/tiles").status_code == 200
    app.extensions["muckebox"] = make_services(ADMIN_PIN="9999")
    assert client.get("/api/admin/tiles").status_code == 401


def test_session_expires(admin, monkeypatch):
    real_time = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() + 13 * 3600)
    assert admin.get("/api/admin/tiles").status_code == 401


def test_logout(admin):
    assert admin.post("/api/admin/logout", headers=POST).status_code == 200
    assert admin.get("/api/admin/tiles").status_code == 401


def test_cross_origin_mutation_is_refused(admin):
    response = admin.post(
        "/api/admin/tiles",
        json={"source": "favorite", "item_id": "FV:2/1"},
        headers={**POST, "Origin": "http://evil.example.com"},
    )
    assert (response.status_code, response.get_json()["error"]["code"]) == (403, "origin_mismatch")


def test_same_origin_mutation_is_allowed(admin):
    response = admin.post(
        "/api/admin/tiles",
        json={"source": "favorite", "item_id": "FV:2/1"},
        headers={**POST, "Origin": "http://localhost"},
    )
    assert response.status_code == 201


def test_bad_login_body(client):
    assert client.post("/api/admin/login", data="x", headers=POST).status_code == 400
    assert client.post("/api/admin/login", json={"pin": 1234}, headers=POST).status_code == 400


# -- status and favorites -------------------------------------------------------------------


def test_status(admin):
    data = admin.get("/api/admin/status").get_json()
    assert data["sonos"]["status"] == "ok"
    assert data["volume_guard"]["max"] == 25
    assert data["source_url"].startswith("https://github.com/")
    assert data["config_problems"] == []


def test_favorites(admin):
    favorites = admin.get("/api/admin/favorites").get_json()["favorites"]
    by_title = {f["title"]: f for f in favorites}
    assert by_title["Kinderradio"]["route"] == "direct"
    assert by_title["Fernseher"]["playable"] is False
    assert by_title["Fernseher"]["reason"] == "tv_input"
    add_favorite(admin, 3)
    refreshed = admin.get("/api/admin/favorites?refresh=1").get_json()["favorites"]
    assert {f["title"]: f for f in refreshed}["Kinderradio"]["tile_id"]


def test_favorite_art(admin):
    response = admin.get("/api/admin/favorite-art?item_id=FV:2/1")
    assert response.mimetype == "image/jpeg"
    assert admin.get("/api/admin/favorite-art?item_id=FV:2/99").status_code == 404


def test_favorites_when_speaker_is_off(admin, fake_sonos):
    from muckebox.sonos.errors import SonosUnreachable

    fake_sonos.fail_next["list_favorites"] = SonosUnreachable()
    response = admin.get("/api/admin/favorites?refresh=1")
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        503,
        "sonos_unreachable",
    )


# -- tiles --------------------------------------------------------------------------------


def test_add_favorite_tile_with_cover(admin, services):
    response = add_favorite(admin, 1)
    assert response.status_code == 201
    data = response.get_json()
    assert data["warnings"] == []
    assert data["tile"]["title"] == "Kinderlieder"
    assert data["tile"]["cover"].startswith("/covers/")
    assert data["tile"]["source"] == {
        "type": "favorite",
        "description": "Apple Music",
        "route": "queue",
    }
    assert status_of(admin, data["tile"]["cover"]) == 200


def test_add_favorite_without_art(admin, fake_sonos):
    from muckebox.sonos.errors import SonosUnreachable

    fake_sonos.fail_next["fetch_art"] = SonosUnreachable()
    data = add_favorite(admin, 2).get_json()
    assert data["warnings"] == ["cover_missing"]
    assert data["tile"]["cover"] is None


def test_unplayable_and_unknown_favorites(admin):
    tv = add_favorite(admin, 6)
    assert (tv.status_code, tv.get_json()["error"]["code"]) == (422, "not_playable")
    missing = add_favorite(admin, 42)
    assert (missing.status_code, missing.get_json()["error"]["code"]) == (404, "favorite_not_found")
    bad = admin.post("/api/admin/tiles", json={"source": "?"}, headers=POST)
    assert bad.status_code == 400


def test_rename_move_delete(admin, services):
    a = add_favorite(admin, 1).get_json()["tile"]
    b = add_favorite(admin, 2).get_json()["tile"]
    rev = admin.get("/api/admin/tiles").get_json()["rev"]

    renamed = admin.patch(
        f"/api/admin/tiles/{a['id']}", json={"title": "Lieder", "rev": rev}, headers=POST
    )
    assert renamed.get_json()["tiles"][0]["title"] == "Lieder"
    rev = renamed.get_json()["rev"]

    moved = admin.post(
        f"/api/admin/tiles/{b['id']}/move", json={"direction": "up", "rev": rev}, headers=POST
    )
    assert [t["id"] for t in moved.get_json()["tiles"]] == [b["id"], a["id"]]
    rev = moved.get_json()["rev"]

    deleted = admin.delete(f"/api/admin/tiles/{a['id']}?rev={rev}", headers=POST)
    assert [t["id"] for t in deleted.get_json()["tiles"]] == [b["id"]]
    # The deleted tile's cover file is gone.
    assert len(list(services.covers.directory.iterdir())) == 1


def test_stale_revision(admin):
    tile = add_favorite(admin, 1).get_json()["tile"]
    response = admin.patch(
        f"/api/admin/tiles/{tile['id']}", json={"title": "X", "rev": 0}, headers=POST
    )
    assert (response.status_code, response.get_json()["error"]["code"]) == (409, "rev_conflict")


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "code"),
    [
        ("patch", "/api/admin/tiles/{id}", {"title": ""}, 422, "title_invalid"),
        ("patch", "/api/admin/tiles/{id}", {"title": 5}, 400, "bad_request"),
        ("patch", "/api/admin/tiles/{id}", {"title": "x", "rev": "abc"}, 400, "bad_request"),
        ("post", "/api/admin/tiles/{id}/move", {"direction": "left"}, 400, "bad_request"),
        ("patch", "/api/admin/tiles/t-missing", {"title": "X"}, 404, "tile_not_found"),
    ],
)
def test_invalid_tile_changes(admin, method, path, body, status, code):
    tile = add_favorite(admin, 1).get_json()["tile"]
    response = getattr(admin, method)(path.format(id=tile["id"]), json=body, headers=POST)
    assert (response.status_code, response.get_json()["error"]["code"]) == (status, code)


# -- cover upload ------------------------------------------------------------------------------


def status_of(client, path):
    with client.get(path) as response:
        return response.status_code


def upload(admin, tile_id, data, filename="photo.png"):
    return admin.put(
        f"/api/admin/tiles/{tile_id}/cover",
        data={"cover": (io.BytesIO(data), filename)},
        headers=POST,
        content_type="multipart/form-data",
    )


def test_upload_cover_replaces_the_old_one(admin, services):
    tile = add_favorite(admin, 1).get_json()["tile"]
    old = tile["cover"]
    response = upload(admin, tile["id"], png(colour=(0, 0, 255)))
    assert response.status_code == 200
    new = response.get_json()["tiles"][0]["cover"]
    assert new != old
    assert status_of(admin, old) == 404  # old file removed
    assert status_of(admin, new) == 200


def test_upload_ignores_the_client_file_name(admin, services):
    tile = add_favorite(admin, 1).get_json()["tile"]
    upload(admin, tile["id"], png(), filename="../../library.json")
    assert (services.settings.data_dir / "library.json").read_text().startswith("{")


@pytest.mark.parametrize("data", [b"not an image", b"<svg xmlns='http://www.w3.org/2000/svg'/>"])
def test_upload_rejects_non_images(admin, data):
    tile = add_favorite(admin, 1).get_json()["tile"]
    response = upload(admin, tile["id"], data)
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        422,
        "upload_not_image",
    )


def test_upload_rejects_large_files(admin):
    tile = add_favorite(admin, 1).get_json()["tile"]
    # Build the multipart body by hand: the test client would spool a large
    # generated body to a temporary file that it never closes.
    boundary = "muckebox-test"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="cover"; filename="big.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + b"x" * (MAX_UPLOAD_BYTES + 1)
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = admin.put(
        f"/api/admin/tiles/{tile['id']}/cover",
        data=body,
        headers=POST,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        413,
        "upload_too_large",
    )


def test_upload_for_unknown_tile_stores_nothing(admin, services):
    response = upload(admin, "t-missing", png())
    assert response.status_code == 404
    assert not services.covers.directory.exists() or not list(services.covers.directory.iterdir())


def test_upload_without_file(admin):
    tile = add_favorite(admin, 1).get_json()["tile"]
    response = admin.put(f"/api/admin/tiles/{tile['id']}/cover", data={}, headers=POST)
    assert response.status_code == 400
