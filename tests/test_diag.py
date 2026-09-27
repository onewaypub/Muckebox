# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import io

import pytest

from muckebox import diag
from muckebox.settings import DEFAULT_MAX_VOLUME
from muckebox.sonos.fake import FakeSonos


@pytest.fixture
def env(tmp_path):
    return {"DATA_DIR": str(tmp_path), "MUCKEBOX_FAKE_SONOS": "1"}


@pytest.fixture
def chosen(make_store):
    """Kinderzimmer chosen on the parents' page, with a limit of 20."""
    store = make_store("Kinderzimmer", "192.0.2.10")
    store.set_volume(20, 2)
    return store


def run(env, *argv, fake=None, monkeypatch=None):
    if fake is not None:
        monkeypatch.setattr(diag, "make_backend", lambda target: fake)
    out = io.StringIO()
    code = diag.main(list(argv), env, out)
    return code, out.getvalue()


def test_status(env, chosen):
    code, out = run(env, "status")
    assert code == 0
    assert "Room:         Kinderzimmer" in out
    assert "Volume:       10" in out


def test_rooms(env, chosen):
    code, out = run(env, "rooms")
    assert code == 0
    assert "Kinderzimmer" in out and "chosen" in out
    assert "Wohnzimmer" in out
    assert "2 rooms" in out


def test_rooms_before_anything_is_set_up(env, tmp_path):
    code, out = run(env, "rooms")
    assert code == 0
    assert "chosen" not in out
    assert list(tmp_path.iterdir()) == []  # diag never creates settings or a PIN


def test_without_a_room_other_commands_explain(env):
    with pytest.raises(SystemExit, match="No room chosen yet"):
        run(env, "status")


def test_room_and_ip_can_be_overridden(env, chosen):
    target = diag.load_target(env, room="Wohnzimmer", ip="192.0.2.99")
    assert (target.room, target.room_uid, target.seed_ip) == ("Wohnzimmer", None, "192.0.2.99")
    same = diag.load_target(env, room="Kinderzimmer")
    assert same.room_uid == chosen.current().room_uid
    assert same.seed_ip == "192.0.2.10"
    assert same.max_volume == 20
    assert run(env, "--room", "Wohnzimmer", "status")[1].startswith("Room:         Wohnzimmer")


def test_defaults_without_settings(env):
    target = diag.load_target(env, room="Kinderzimmer")
    assert (target.room_uid, target.seed_ip) == (None, None)
    assert target.max_volume == DEFAULT_MAX_VOLUME


def test_real_backend_uses_the_stored_room(chosen, tmp_path):
    from muckebox.sonos.soco_backend import SocoBackend

    target = diag.load_target({"DATA_DIR": str(tmp_path)})
    backend = diag.make_backend(target)
    assert isinstance(backend, SocoBackend)


def test_favorites_show_route_and_reason(env, chosen):
    code, out = run(env, "favorites")
    assert code == 0
    assert "direct" in out and "queue" in out
    assert "unsupported (tv_input)" in out
    assert "6 favorites" in out


def test_play(env, chosen, monkeypatch):
    fake = FakeSonos()
    code, out = run(env, "play", "FV:2/3", fake=fake, monkeypatch=monkeypatch)
    assert code == 0
    assert "via direct" in out
    assert fake.state == "playing"


def test_play_unknown_favorite(env, chosen):
    with pytest.raises(SystemExit):
        run(env, "play", "FV:2/99")


def test_errors_are_explained(env, chosen, monkeypatch):
    fake = FakeSonos(reachable=False)
    code, out = run(env, "status", fake=fake, monkeypatch=monkeypatch)
    assert code == 1
    assert "sonos_unreachable" in out
    assert "nicht erreichbar" in out


def test_watch_volume_uses_the_saved_limit(env, chosen, monkeypatch):
    fake = FakeSonos(volume=80)
    monkeypatch.setattr(diag.time, "sleep", lambda s: None)
    code, out = run(env, "watch-volume", "--seconds", "0", fake=fake, monkeypatch=monkeypatch)
    assert code == 0
    assert "limit 20" in out


def test_watch_volume_reports_corrections():
    fake = FakeSonos(volume=80)
    out = io.StringIO()
    args = argparse.Namespace(seconds=1, max=25, limit=20)
    diag.cmd_watch_volume(fake, out, args, sleep=lambda s: None)
    assert "corrected to 25" in out.getvalue()
    assert fake.volume == 25
