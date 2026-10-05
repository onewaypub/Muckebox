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
    login = auth.RateLimiter()
    monkeypatch.setattr(auth, "login_limiter", login)
    monkeypatch.setattr(auth, "override_limiter", auth.RateLimiter(share_global_with=login))


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


# -- login, session --------------------------------------------------------------------


def test_session_state(client):
    assert client.get("/api/admin/session").get_json() == {"ok": True, "logged_in": False}


def test_generated_pin_works_until_it_is_changed(make_services):
    services = make_services(pin=None)
    client = create_app(services).test_client()
    generated = services.store.current().pin.generated
    assert login(client, generated).status_code == 200


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


def fail(limiter, client):
    assert limiter.begin(client) is None
    limiter.finish(client, ok=False)


def test_global_rate_limit():
    limiter = auth.RateLimiter(clock=lambda: 0.0)
    for number in range(auth.MAX_FAILURES_GLOBAL):
        fail(limiter, f"192.0.2.{number}")
    assert limiter.begin("198.51.100.1") is not None


def test_running_checks_count_against_the_limit():
    """Parallel requests must not get more guesses while scrypt runs."""
    limiter = auth.RateLimiter(clock=lambda: 0.0)
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        assert limiter.begin("192.0.2.5") is None
    assert limiter.begin("192.0.2.5") == 1  # try again once the running ones finish
    assert limiter.begin("192.0.2.6") is None  # other clients are not affected
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        limiter.finish("192.0.2.5", ok=False)
    assert limiter.begin("192.0.2.5") > 1


def test_rate_limit_expires():
    now = {"t": 0.0}
    limiter = auth.RateLimiter(clock=lambda: now["t"])
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        fail(limiter, "192.0.2.5")
    assert limiter.begin("192.0.2.5") is not None
    now["t"] = auth.FAILURE_WINDOW + 1
    assert limiter.begin("192.0.2.5") is None


def test_a_pin_changed_elsewhere_ends_sessions(admin, services, tmp_path, monkeypatch):
    from muckebox.settings import SettingsStore

    from .conftest import TEST_SCRYPT

    monkeypatch.setattr("muckebox.settings.RELOAD_INTERVAL", 0)
    assert admin.get("/api/admin/tiles").status_code == 200
    new_pin = SettingsStore(tmp_path, scrypt=TEST_SCRYPT).reset_pin()  # e.g. reset-pin
    assert admin.get("/api/admin/tiles").status_code == 401
    assert login(admin, "2468").status_code == 401
    assert login(admin, new_pin).status_code == 200


def test_a_new_pin_clears_the_rate_limit(client, services, tmp_path, monkeypatch):
    from muckebox.settings import SettingsStore

    from .conftest import TEST_SCRYPT

    monkeypatch.setattr("muckebox.settings.RELOAD_INTERVAL", 0)
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        login(client, "9999")
    assert login(client).status_code == 429
    new_pin = SettingsStore(tmp_path, scrypt=TEST_SCRYPT).reset_pin()
    assert login(client, new_pin).status_code == 200


def test_overlong_pins_are_refused_without_hashing(client, services, monkeypatch):
    monkeypatch.setattr(services.store, "verify_pin", lambda pin: pytest.fail("hashed"))
    response = login(client, "7" * 1000)
    assert (response.status_code, response.get_json()["error"]["code"]) == (401, "pin_wrong")


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


# -- review regressions ---------------------------------------------------------------


def test_session_cookie_lasts_12_hours_and_is_not_renewed(client, services):
    from datetime import UTC, datetime
    from email.utils import parsedate_to_datetime

    response = login(client)
    cookie = response.headers["Set-Cookie"]
    expires = parsedate_to_datetime(cookie.split("Expires=")[1].split(";")[0])
    hours = (expires - datetime.now(UTC)).total_seconds() / 3600
    assert 11.9 < hours <= 12.01
    assert "Set-Cookie" not in client.get("/api/admin/tiles").headers
    assert "Set-Cookie" not in client.get("/api/state").headers


def test_adding_the_same_favorite_twice_keeps_one_tile(admin):
    first = add_favorite(admin, 1).get_json()
    second = add_favorite(admin, 1).get_json()
    assert second["tile"]["id"] == first["tile"]["id"]
    assert len(second["tiles"]) == 1


def test_unavailable_art_is_not_fetched_again(admin, fake_sonos):
    from muckebox.sonos.errors import SonosUnreachable

    fake_sonos.fail_next["fetch_art"] = SonosUnreachable()
    assert admin.get("/api/admin/favorite-art?item_id=FV:2/1").status_code == 404
    fake_sonos.calls.clear()
    assert admin.get("/api/admin/favorite-art?item_id=FV:2/1").status_code == 404
    assert not [c for c in fake_sonos.calls if c[0] == "fetch_art"]


def test_oversized_upload_names_the_limit(admin):
    tile = add_favorite(admin, 1).get_json()["tile"]
    response = admin.put(
        f"/api/admin/tiles/{tile['id']}/cover",
        data=b"x",
        headers=POST,
        content_type="multipart/form-data; boundary=x",
        environ_overrides={"CONTENT_LENGTH": str(30 * 1024 * 1024)},
    )
    assert (response.status_code, response.get_json()["error"]["code"]) == (
        413,
        "upload_too_large",
    )


def test_rate_limiter_forgets_old_failures():
    now = {"t": 0.0}
    limiter = auth.RateLimiter(clock=lambda: now["t"])
    fail(limiter, "192.0.2.5")
    now["t"] = auth.FAILURE_WINDOW + 1
    limiter.begin("192.0.2.6")
    assert limiter._per_client == {}
    assert not limiter._global


def test_status_reports_a_corrupt_library_as_a_code(make_services, tmp_path):
    (tmp_path / "library.json").write_text("{broken")
    client = create_app(make_services()).test_client()
    login(client)
    problem = client.get("/api/admin/status").get_json()["library_problem"]
    assert problem["code"] == "library_corrupt"


# -- settings ------------------------------------------------------------------------


def error_code(response):
    return response.status_code, response.get_json()["error"]["code"]


def test_settings_never_contain_pin_data(admin, services):
    data = admin.get("/api/admin/settings").get_json()
    settings = data["settings"]
    assert {key: settings[key] for key in list(settings)[:6]} == {
        "room": "Kinderzimmer",
        "seed_ip": None,
        "max_volume": 25,
        "volume_step": 3,
        "pin_generated": False,
        "time_zone": None,
    }
    sections = {"schedule", "sleep_timer", "games", "controls", "hue"}
    assert set(settings) - sections == set(list(settings)[:6])
    assert data["sonos"]["status"] == "ok"
    text = admin.get("/api/admin/settings").get_data(as_text=True)
    pin = services.store.current().pin
    for secret in ("salt", "hash", pin.salt, pin.hash, "2468"):
        assert secret not in text


@pytest.mark.parametrize(
    "path", ["/api/admin/settings/volume", "/api/admin/settings/room", "/api/admin/pin"]
)
def test_settings_changes_need_login_and_csrf_header(client, admin, path):
    method = admin.post if path == "/api/admin/pin" else admin.put
    assert error_code(method(path, json={})) == (403, "csrf_header_missing")
    assert admin.post("/api/admin/logout", headers=POST).status_code == 200
    assert error_code(method(path, json={}, headers=POST)) == (401, "login_required")


def test_volume_settings_apply_at_once(admin, services):
    response = admin.put(
        "/api/admin/settings/volume", json={"max_volume": 18, "volume_step": 2}, headers=POST
    )
    assert response.status_code == 200
    assert response.get_json()["settings"]["max_volume"] == 18
    assert services.runtime.state_document()["volume"] == {
        "value": None,
        "max": 18,
        "limit": 18,
        "step": 2,
    }


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"max_volume": 0, "volume_step": 1}, "max_volume_invalid"),
        ({"max_volume": "20", "volume_step": 2}, "max_volume_invalid"),
        ({"max_volume": 10, "volume_step": 11}, "volume_step_invalid"),
        ({"max_volume": 10}, "volume_step_invalid"),
    ],
)
def test_invalid_volume_settings(admin, services, body, code):
    response = admin.put("/api/admin/settings/volume", json=body, headers=POST)
    assert error_code(response) == (422, code)
    assert services.store.current().max_volume == 25


def test_room_search_lists_rooms(admin):
    response = admin.post("/api/admin/rooms/search", json={}, headers=POST)
    assert response.get_json()["rooms"] == [
        {"name": "Kinderzimmer", "ip": "192.0.2.10", "grouped": False, "chosen": True},
        {"name": "Wohnzimmer", "ip": "192.0.2.11", "grouped": False, "chosen": False},
    ]


def test_room_search_with_a_speaker_ip(admin, household):
    body = {"seed_ip": " 192.0.2.11 ", "refresh": True}
    assert admin.post("/api/admin/rooms/search", json=body, headers=POST).status_code == 200
    assert household.searches == ["192.0.2.11"]


def test_room_search_problems(admin, household):
    response = admin.post("/api/admin/rooms/search", json={"seed_ip": "http://x"}, headers=POST)
    assert error_code(response) == (422, "seed_ip_invalid")
    household.reachable = False
    response = admin.post("/api/admin/rooms/search", json={}, headers=POST)
    assert error_code(response) == (503, "sonos_unreachable")


def test_choosing_a_room(admin, services, household):
    response = admin.put(
        "/api/admin/settings/room", json={"room": "Wohnzimmer", "seed_ip": ""}, headers=POST
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["settings"]["room"] == "Wohnzimmer"
    assert data["sonos"] == {"status": "ok", "room": "Wohnzimmer", "grouped": False}
    assert services.store.current().room_uid == household.uid("Wohnzimmer")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"room": "Keller"}, (503, "room_not_found")),
        ({"room": ""}, (422, "room_invalid")),
        ({"room": 5}, (422, "room_invalid")),
        ({"room": "Wohnzimmer", "seed_ip": "192.0.2.1:1400"}, (422, "seed_ip_invalid")),
    ],
)
def test_room_that_cannot_be_chosen(admin, services, body, expected):
    response = admin.put("/api/admin/settings/room", json=body, headers=POST)
    assert error_code(response) == expected
    assert services.store.current().room == "Kinderzimmer"


def test_room_that_cannot_be_saved(admin, services, monkeypatch):
    def broken(*args):
        raise OSError("read-only file system")

    monkeypatch.setattr(services.store, "set_room", broken)
    response = admin.put("/api/admin/settings/room", json={"room": "Wohnzimmer"}, headers=POST)
    assert error_code(response) == (500, "settings_save_failed")
    assert services.runtime.state_document()["sonos"]["room"] == "Kinderzimmer"


def test_first_start_without_a_room(make_services):
    services = make_services(room=None, pin=None)
    client = create_app(services).test_client()
    assert login(client, services.store.current().pin.generated).status_code == 200
    data = client.get("/api/admin/settings").get_json()
    assert data["settings"]["room"] is None
    assert data["settings"]["pin_generated"] is True
    assert data["sonos"]["status"] == "not_configured"
    status = client.get("/api/admin/status").get_json()
    assert status["config_problems"] == ["not_configured", "pin_generated"]
    assert error_code(client.get("/api/admin/favorites")) == (503, "not_configured")
    response = client.put("/api/admin/settings/room", json={"room": "Kinderzimmer"}, headers=POST)
    assert response.get_json()["sonos"]["status"] == "ok"
    assert client.get("/api/admin/favorites").status_code == 200


# -- changing the PIN ------------------------------------------------------------------


def change_pin(client, current="2468", new="9753"):
    return client.post("/api/admin/pin", json={"current": current, "new": new}, headers=POST)


def test_changing_the_pin_ends_all_other_sessions(admin, app, tmp_path):
    other = app.test_client()
    assert login(other).status_code == 200
    response = change_pin(admin)
    assert response.status_code == 200
    assert response.get_json()["settings"]["pin_generated"] is False
    assert admin.get("/api/admin/tiles").status_code == 200  # this session stays
    assert other.get("/api/admin/tiles").status_code == 401
    assert login(other, "2468").status_code == 401
    assert login(other, "9753").status_code == 200
    assert "9753" not in (tmp_path / "settings.json").read_text()


def test_changing_the_pin_needs_the_current_one(admin, services):
    response = change_pin(admin, current="1111")
    assert error_code(response) == (403, "pin_wrong")  # 403: still logged in
    assert services.store.verify_pin("2468")
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        change_pin(admin, current="1111")
    assert error_code(change_pin(admin)) == (429, "pin_rate_limited")


@pytest.mark.parametrize(
    ("new", "code"),
    [
        ("12", "pin_too_short"),
        ("1234", "pin_placeholder"),
        ("x" * 65, "pin_invalid"),
        (None, "pin_invalid"),
    ],
)
def test_new_pin_rules(admin, services, new, code):
    assert error_code(change_pin(admin, new=new)) == (422, code)
    assert services.store.verify_pin("2468")


def check_then(services, monkeypatch, meanwhile):
    """Run ``meanwhile`` after the PIN check but before the request ends."""
    real_check = services.store.check

    def check(pin):
        version = real_check(pin)
        meanwhile()
        return version

    monkeypatch.setattr(services.store, "check", check)


def test_a_login_during_a_pin_change_gets_no_session(client, services, monkeypatch):
    check_then(services, monkeypatch, lambda: services.store.change_pin("9753"))
    assert login(client).status_code == 200  # the old PIN was right when it was checked
    assert client.get("/api/admin/tiles").status_code == 401


def test_pin_change_is_refused_after_a_reset_meanwhile(admin, services, monkeypatch):
    check_then(services, monkeypatch, services.store.reset_pin)
    assert error_code(change_pin(admin)) == (409, "pin_changed")
    assert not services.store.verify_pin("9753")


def test_unencodable_pins(client, admin):
    body = b'{"pin": "\\ud800"}'
    other = client.application.test_client()
    response = other.post(
        "/api/admin/login", data=body, headers={**POST, "Content-Type": "application/json"}
    )
    assert error_code(response) == (401, "pin_wrong")
    assert error_code(change_pin(admin, new="12\ud80034")) == (422, "pin_invalid")


def test_status_shows_the_local_time(admin):
    time = admin.get("/api/admin/status").get_json()["time"]
    assert set(time) == {"now", "zone", "source"}
    assert time["now"][10] == "T"


def test_parents_choose_the_time_zone(admin, services):
    response = admin.put(
        "/api/admin/settings/time-zone", json={"zone": "Europe/Lisbon"}, headers=POST
    )
    assert response.get_json()["settings"]["time_zone"] == "Europe/Lisbon"
    assert admin.get("/api/admin/status").get_json()["time"]["zone"] == "Europe/Lisbon"
    bad = admin.put("/api/admin/settings/time-zone", json={"zone": "../x"}, headers=POST)
    assert error_code(bad) == (422, "time_zone_invalid")


# -- usage times and override ------------------------------------------------------------

DAILY = {
    "enabled": True,
    "fade_minutes": 10,
    "days": {
        day: {"from": "07:00", "to": "19:00"}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    },
}


def set_evening(services, text="2026-09-28 19:30"):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    services.store.set_time_zone("Europe/Berlin")
    services.runtime.clock.set_time(
        datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    )


def test_parents_set_the_usage_times(admin, services):
    response = admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    assert response.get_json()["settings"]["schedule"] == DAILY
    bad = {**DAILY, "days": {**DAILY["days"], "mon": {"from": "20:00", "to": "07:00"}}}
    response = admin.put("/api/admin/settings/schedule", json=bad, headers=POST)
    assert error_code(response) == (422, "schedule_order_invalid")


def test_bedtime_refuses_kids_commands_with_409(admin, client, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    set_evening(services)
    tile = add_favorite(admin, 3).get_json()["tile"]
    response = client.post(f"/api/tiles/{tile['id']}/play", headers=POST)
    assert error_code(response) == (409, "bedtime")
    state = client.get("/api/state").get_json()
    assert state["schedule"]["phase"] == "closed"
    assert admin.get("/api/admin/status").get_json()["schedule"]["phase"] == "closed"


def test_parents_allow_more_time(admin, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    set_evening(services)
    response = admin.post("/api/admin/override", json={"minutes": 30}, headers=POST)
    schedule = response.get_json()["schedule"]
    assert schedule["phase"] == "open"
    assert schedule["override_until"] == int(services.runtime.clock.time()) + 1800
    response = admin.delete("/api/admin/override", headers=POST)
    schedule = response.get_json()["schedule"]
    assert schedule["phase"] == "fading"  # ends gently: fade, then pause
    assert schedule["override_until"] == int(services.runtime.clock.time()) + 600
    morning = admin.post("/api/admin/override", json={"until": "morning"}, headers=POST)
    assert morning.get_json()["schedule"]["phase"] == "open"
    assert admin.post("/api/admin/override", json={"minutes": 7}, headers=POST).status_code == 400


def test_override_from_the_kids_tablet(admin, client, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    set_evening(services)
    tablet = client.application.test_client()
    body = {"pin": "2468", "minutes": 15}
    response = tablet.post("/api/override", json=body, headers=POST)
    assert response.get_json()["schedule"]["phase"] == "open"
    assert tablet.get("/api/admin/settings").status_code == 401  # no session for the tablet


def test_pin_pad_mashing_does_not_lock_the_parents_out(admin, client, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    set_evening(services)
    tablet = client.application.test_client()
    for _ in range(auth.MAX_FAILURES_PER_CLIENT):
        tablet.post("/api/override", json={"pin": "1111", "minutes": 15}, headers=POST)
    blocked = tablet.post("/api/override", json={"pin": "2468", "minutes": 15}, headers=POST)
    assert error_code(blocked) == (429, "pin_rate_limited")
    assert login(tablet).status_code == 200  # the parents' page has its own counter


@pytest.mark.parametrize(
    "body", [{"pin": "2468"}, {"pin": "2468", "minutes": 45}, {"pin": "2468", "until": "noon"}, []]
)
def test_invalid_override_requests(client, body):
    assert client.post("/api/override", json=body, headers=POST).status_code == 400


def test_override_without_usage_times(client):
    response = client.post("/api/override", json={"pin": "2468", "minutes": 15}, headers=POST)
    assert error_code(response) == (409, "schedule_off")


# -- sleep timer --------------------------------------------------------------------------


def test_parents_set_up_the_sleep_timer(admin, client, services):
    body = {"enabled": True, "minutes": 20, "wake": "06:45", "lights": "off"}
    response = admin.put("/api/admin/settings/sleep-timer", json=body, headers=POST)
    assert response.get_json()["settings"]["sleep_timer"] == body
    bad = admin.put("/api/admin/settings/sleep-timer", json={"minutes": 200}, headers=POST)
    assert error_code(bad) == (422, "sleep_timer_invalid")


def test_kids_start_the_sleep_timer_and_parents_end_it(admin, client, services):
    started = client.post("/api/sleep-timer/start", headers=POST)
    assert error_code(started) == (409, "sleep_timer_off")
    admin.put(
        "/api/admin/settings/sleep-timer", json={"enabled": True, "minutes": 20}, headers=POST
    )
    timer = client.post("/api/sleep-timer/start", headers=POST).get_json()["sleep_timer"]
    assert timer["ends_at"] == int(services.runtime.clock.time()) + 1200
    assert client.get("/api/state").get_json()["sleep_timer"]["ends_at"] == timer["ends_at"]
    assert admin.get("/api/admin/status").get_json()["sleep_timer"]["ends_at"] == timer["ends_at"]
    ended = admin.delete("/api/admin/sleep-timer", headers=POST).get_json()
    assert ended["sleep_timer"]["ends_at"] is None


def test_the_sleep_timer_cannot_start_at_bedtime(admin, client, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    admin.put(
        "/api/admin/settings/sleep-timer", json={"enabled": True, "minutes": 20}, headers=POST
    )
    set_evening(services)
    assert error_code(client.post("/api/sleep-timer/start", headers=POST)) == (409, "bedtime")


# -- Weiterhören ------------------------------------------------------------------------


def test_tiles_show_and_change_resume(admin, services, fake_sonos):
    album = add_favorite(admin, 2).get_json()["tile"]  # "Hörspiel Folge 1", an album
    radio = add_favorite(admin, 3).get_json()["tile"]
    assert album["resume"] == {
        "available": True,
        "enabled": True,
        "default": True,
        "position": None,
    }
    assert radio["resume"]["available"] is False
    from muckebox.sonos.model import Position

    services.runtime.resume.record(album["id"], Position(3, 760, 1200, "x"), 5)
    tiles = admin.get("/api/admin/tiles").get_json()
    shown = next(tile for tile in tiles["tiles"] if tile["id"] == album["id"])
    assert shown["resume"]["position"] == {"track": 3, "seconds": 760}
    response = admin.delete(f"/api/admin/tiles/{album['id']}/position", headers=POST)
    shown = next(tile for tile in response.get_json()["tiles"] if tile["id"] == album["id"])
    assert shown["resume"]["position"] is None
    body = {"enabled": False, "rev": response.get_json()["rev"]}
    response = admin.put(f"/api/admin/tiles/{album['id']}/resume", json=body, headers=POST)
    shown = next(tile for tile in response.get_json()["tiles"] if tile["id"] == album["id"])
    assert shown["resume"]["enabled"] is False
    assert (
        admin.put(
            f"/api/admin/tiles/{album['id']}/resume", json={"enabled": "no"}, headers=POST
        ).status_code
        == 400
    )
    assert admin.delete("/api/admin/tiles/t-missing/position", headers=POST).status_code == 404


# -- games -------------------------------------------------------------------------------


def test_parents_enable_games_and_kids_start_them(admin, client, services):
    tile = add_favorite(admin, 1).get_json()["tile"]
    body = {
        "daily_minutes": 20,
        "dance_tile": tile["id"],
        "items": {"sound_quiz": {"enabled": True, "level": 1}},
    }
    response = admin.put("/api/admin/settings/games", json=body, headers=POST)
    assert response.get_json()["settings"]["games"]["items"]["sound_quiz"] == {
        "enabled": True,
        "level": 1,
    }
    started = client.post("/api/games/sound_quiz/start", headers=POST).get_json()["game"]
    assert (started["id"], started["level"], started["seconds"]) == ("sound_quiz", 1, 180)
    assert client.post("/api/games/end", headers=POST).get_json()["games"]["active"] is None
    status = admin.get("/api/admin/status").get_json()["games"]
    assert status["daily_seconds"] == 1200


def test_game_errors(admin, client):
    assert client.post("/api/games/chess/start", headers=POST).status_code == 404
    response = client.post("/api/games/sound_quiz/start", headers=POST)
    assert error_code(response) == (409, "game_unavailable")
    response = client.post("/api/games/freeze_dance/mute", json={"muted": "yes"}, headers=POST)
    assert response.status_code == 400
    response = client.post("/api/games/freeze_dance/mute", json={"muted": True}, headers=POST)
    assert error_code(response) == (409, "game_unavailable")
    unknown_tile = {"dance_tile": "t0000000000000000"[:16]}
    response = admin.put("/api/admin/settings/games", json=unknown_tile, headers=POST)
    assert error_code(response) == (422, "games_invalid")


def test_the_pin_pad_and_the_login_share_one_total(admin, client, services):
    admin.put("/api/admin/settings/schedule", json=DAILY, headers=POST)
    set_evening(services)
    for number in range(auth.MAX_FAILURES_GLOBAL // 2):
        address = {"REMOTE_ADDR": f"192.0.2.{number + 1}"}
        client.post(
            "/api/override", json={"pin": "1111", "minutes": 15}, headers=POST, environ_base=address
        )
        login(client, "1111", environ_base=address)
    response = login(client, environ_base={"REMOTE_ADDR": "198.51.100.7"})
    assert error_code(response) == (429, "pin_rate_limited")  # 20 wrong PINs in total


def test_parents_set_the_tap_wait_and_the_pause_without_taps(admin, services):
    body = {"tap_cooldown": 8, "idle_minutes": 90, "profile": "big", "skip_buttons": True}
    response = admin.put("/api/admin/settings/controls", json=body, headers=POST)
    assert response.get_json()["settings"]["controls"] == body
    assert services.store.current().controls.idle_minutes == 90
    status = admin.get("/api/admin/status").get_json()
    assert status["idle"] == {"minutes": 90, "paused_at": None}
    response = admin.put("/api/admin/settings/controls", json={"tap_cooldown": 60}, headers=POST)
    assert error_code(response) == (422, "controls_invalid")


# -- Hue lights -------------------------------------------------------------------------------


def pair_and_choose(admin, fake_hue):
    response = admin.post("/api/admin/hue/pair", json={"ip": fake_hue.bridge.ip}, headers=POST)
    assert response.status_code == 200
    slots = [
        {"scene": "scene-bright", "picture": "sun"},
        {"scene": "scene-night", "picture": "moon"},
    ]
    body = {"room": "room-kids", "slots": slots}
    return admin.put("/api/admin/settings/hue", json=body, headers=POST)


def test_parents_find_pair_and_choose_lights(admin, client, fake_hue, services):
    found = admin.post("/api/admin/hue/search", json={"ip": None}, headers=POST).get_json()
    assert found["bridges"] == [
        {"ip": "192.0.2.50", "id": "001788fffe000001", "name": "Hue Bridge"}
    ]
    response = pair_and_choose(admin, fake_hue)
    hue = response.get_json()["settings"]["hue"]
    assert hue["bridge"] == {"ip": "192.0.2.50", "id": "001788fffe000001", "name": "Hue Bridge"}
    assert [s["picture"] for s in hue["slots"]] == ["sun", "moon"]
    view = admin.get("/api/admin/hue").get_json()
    assert {r["name"] for r in view["rooms"]} == {"Kinderzimmer", "Wohnzimmer"}
    assert view["available"] is True
    # The kids switch the night light on and off again.
    services.runtime.lights.poll()
    lights = client.get("/api/state").get_json()["lights"]
    assert [s["picture"] for s in lights["slots"]] == ["sun", "moon"]
    response = client.post("/api/lights/2/toggle", headers=POST)
    assert response.get_json()["lights"]["slots"][1]["active"] is True
    again = client.post("/api/lights/2/toggle", headers=POST)
    assert error_code(again) == (409, "cooling_down")
    assert client.post("/api/lights/9/toggle", headers=POST).status_code == 404
    status = admin.get("/api/admin/status").get_json()["lights"]
    assert status == {
        "configured": True,
        "bridge": "Hue Bridge",
        "available": True,
        "problem": None,
    }


def test_secrets_of_the_bridge_never_leave_the_server(admin, fake_hue):
    pair_and_choose(admin, fake_hue)
    for path in ("/api/admin/settings", "/api/admin/hue", "/api/admin/status"):
        text = admin.get(path).get_data(as_text=True)
        assert fake_hue.bridge.key not in text
        assert fake_hue.bridge.fingerprint not in text


def test_pairing_before_the_button_writes_nothing(admin, fake_hue, services):
    fake_hue.bridge.button_pressed = False
    response = admin.post("/api/admin/hue/pair", json={"ip": fake_hue.bridge.ip}, headers=POST)
    assert error_code(response) == (409, "hue_link_button")
    assert services.store.current().hue.bridge is None
    bad = admin.post("/api/admin/hue/pair", json={"ip": "http://x"}, headers=POST)
    assert error_code(bad) == (422, "hue_ip_invalid")
    gone = admin.post("/api/admin/hue/pair", json={"ip": "192.0.2.99"}, headers=POST)
    assert error_code(gone)[1] == "hue_unreachable"


def test_reconnect_and_forget(admin, fake_hue, services):
    pair_and_choose(admin, fake_hue)
    fake_hue.bridge.fingerprint = "cd" * 32
    assert admin.post("/api/admin/hue/reconnect", headers=POST).status_code == 200
    assert services.store.current().hue.bridge.fingerprint == "cd" * 32
    response = admin.delete("/api/admin/hue", headers=POST)
    assert response.get_json()["settings"]["hue"]["bridge"] is None
    bad = admin.put(
        "/api/admin/settings/hue", json={"slots": [{"scene": "s", "picture": "x"}]}, headers=POST
    )
    assert error_code(bad) == (422, "hue_invalid")


def test_lights_need_a_bridge(client):
    response = client.post("/api/lights/1/toggle", headers=POST)
    assert error_code(response) == (409, "hue_not_configured")
