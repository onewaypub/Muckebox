# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import os
import stat

import pytest

from muckebox import settings as settings_module
from muckebox.settings import (
    DEFAULT_MAX_VOLUME,
    DEFAULT_VOLUME_STEP,
    SettingsError,
    SettingsFileError,
    SettingsStore,
    validate_seed_ip,
)

CHEAP = {"n": 2**4, "r": 8, "p": 1}  # fast scrypt for tests


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def store(tmp_path, clock=None, **kwargs):
    return SettingsStore(tmp_path, scrypt=CHEAP, clock=clock or Clock(), **kwargs)


def test_first_start_creates_defaults_and_a_generated_pin(tmp_path):
    s = store(tmp_path).current()
    assert (s.room, s.room_uid, s.seed_ip) == (None, None, None)
    assert (s.max_volume, s.volume_step) == (DEFAULT_MAX_VOLUME, DEFAULT_VOLUME_STEP)
    assert not s.configured
    assert s.pin_generated
    assert len(s.pin.generated) == 6 and s.pin.generated.isdigit()
    assert stat.S_IMODE((tmp_path / "settings.json").stat().st_mode) == 0o600


def test_generated_pin_is_stable_across_restarts(tmp_path):
    first = store(tmp_path).current().pin.generated
    assert store(tmp_path).current().pin.generated == first


def test_generated_pin_is_never_in_repr(tmp_path):
    s = store(tmp_path).current()
    assert s.pin.generated not in repr(s)


def test_pin_is_stored_hashed_and_verified(tmp_path):
    st = store(tmp_path)
    st.change_pin("  my pin 42 ")
    raw = (tmp_path / "settings.json").read_text()
    assert "my pin 42" not in raw
    assert json.loads(raw)["admin"]["generated_pin"] is None
    assert st.verify_pin("my pin 42")
    assert st.verify_pin(" my pin 42")
    assert not st.verify_pin("my pin 43")
    assert not st.verify_pin(None)
    assert not st.current().pin_generated


@pytest.mark.parametrize(
    ("pin", "code"),
    [
        ("123", "pin_too_short"),
        ("   ", "pin_too_short"),
        ("1234", "pin_placeholder"),
        ("CHANGE-ME", "pin_placeholder"),
        ("x" * 65, "pin_invalid"),
        ("ab\x00cd", "pin_invalid"),
        (1234, "pin_invalid"),
    ],
)
def test_pin_rules(tmp_path, pin, code):
    with pytest.raises(SettingsError) as info:
        store(tmp_path).change_pin(pin)
    assert info.value.code == code


def test_pin_change_changes_the_version(tmp_path):
    st = store(tmp_path)
    before = st.current().pin.version
    st.change_pin("2468")
    assert st.current().pin.version != before


def test_reset_pin_generates_a_new_logged_pin(tmp_path):
    st = store(tmp_path)
    st.change_pin("2468")
    new = st.reset_pin()
    assert st.current().pin.generated == new
    assert st.verify_pin(new)
    assert not st.verify_pin("2468")


def test_room_and_volume(tmp_path):
    st = store(tmp_path)
    st.set_room(" Kinderzimmer ", "RINCON_000000000000001400", " 192.0.2.10 ")
    st.set_volume(30, 5)
    s = SettingsStore(tmp_path, scrypt=CHEAP).current()
    assert (s.room, s.room_uid, s.seed_ip) == (
        "Kinderzimmer",
        "RINCON_000000000000001400",
        "192.0.2.10",
    )
    assert (s.max_volume, s.volume_step) == (30, 5)
    assert s.configured


@pytest.mark.parametrize(
    ("max_volume", "step", "code"),
    [
        (0, 3, "max_volume_invalid"),
        (101, 3, "max_volume_invalid"),
        (25.0, 3, "max_volume_invalid"),
        (True, 1, "max_volume_invalid"),
        ("25", 3, "max_volume_invalid"),
        (25, 0, "volume_step_invalid"),
        (10, 11, "volume_step_invalid"),
        (25, 2.5, "volume_step_invalid"),
    ],
)
def test_volume_rules(tmp_path, max_volume, step, code):
    with pytest.raises(SettingsError) as info:
        store(tmp_path).set_volume(max_volume, step)
    assert info.value.code == code


@pytest.mark.parametrize("room", ["", "   ", "x" * 101, "a\nb", None, 5])
def test_room_rules(tmp_path, room):
    with pytest.raises(SettingsError) as info:
        store(tmp_path).set_room(room, None, None)
    assert info.value.code == "room_invalid"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("192.0.2.10", "192.0.2.10"),
        ("speaker.example", "speaker.example"),
        ("kids-speaker", "kids-speaker"),
    ],
)
def test_valid_seed_ips(value, expected):
    assert validate_seed_ip(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "192.0.2.300",
        "192.0.2",
        "2001:db8::1",
        "http://192.0.2.10",
        "192.0.2.10:1400",
        "bad host",
        "-x",
        5,
    ],
)
def test_invalid_seed_ips(value):
    with pytest.raises(SettingsError):
        validate_seed_ip(value)


def test_changes_by_another_process_are_picked_up(tmp_path, clock):
    server = store(tmp_path, clock)
    other = store(tmp_path)
    new_pin = other.reset_pin()
    assert not server.verify_pin(new_pin)  # not re-read yet (checked at most once a second)
    clock.now += 1.5
    assert server.verify_pin(new_pin)


def test_updates_start_from_the_file_so_a_reset_is_never_lost(tmp_path, clock):
    server = store(tmp_path, clock)
    server.change_pin("2468")
    new_pin = store(tmp_path).reset_pin()  # e.g. from the command line
    server.set_volume(20, 2)  # the server has not noticed the reset yet
    assert SettingsStore(tmp_path, scrypt=CHEAP).verify_pin(new_pin)


def test_unreadable_file_at_runtime_keeps_the_last_settings(tmp_path, clock):
    server = store(tmp_path, clock)
    server.set_volume(20, 2)
    (tmp_path / "settings.json").write_text("{broken")
    clock.now += 2
    assert server.current().max_volume == 20


def test_corrupt_file_at_startup_is_moved_aside(tmp_path):
    (tmp_path / "settings.json").write_text("{broken")
    st = store(tmp_path)
    assert st.load_problem == "settings_corrupt"
    assert list(tmp_path.glob("settings.json.corrupt-*"))
    assert st.current().pin_generated


def test_file_from_a_newer_version_is_left_alone(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema": 99}))
    with pytest.raises(SettingsFileError, match="newer"):
        store(tmp_path)
    assert json.loads(path.read_text()) == {"schema": 99}


def test_read_only_mode_creates_nothing(tmp_path):
    with pytest.raises(SettingsFileError):
        store(tmp_path, create=False)
    assert list(tmp_path.iterdir()) == []
    store(tmp_path)
    assert store(tmp_path, create=False).current().pin_generated


def test_root_hands_the_file_back_to_its_owner(tmp_path, monkeypatch):
    store(tmp_path)
    chowned = []
    monkeypatch.setattr(settings_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(settings_module.os, "chown", lambda path, uid, gid: chowned.append(path))
    SettingsStore(tmp_path, scrypt=CHEAP).reset_pin()
    assert tmp_path / "settings.json" in chowned


# -- files that were edited or damaged by hand ------------------------------------------


def edit(tmp_path, change):
    path = tmp_path / "settings.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


BROKEN = {
    "sections missing": lambda d: d.clear() or d.update({"schema": 1, "sonos": None}),
    "sonos is null": lambda d: d.update(sonos=None),
    "limit too high": lambda d: d["volume"].update(max=150),
    "limit is a float": lambda d: d["volume"].update(max=1e300),
    "limit is true": lambda d: d["volume"].update(max=True),
    "room with a newline": lambda d: d["sonos"].update(room="a\nb"),
    "seed is a URL": lambda d: d["sonos"].update(seed_ip="http://x"),
    "hash is not hex": lambda d: d["admin"]["pin"].update(hash="zz"),
    "huge scrypt cost": lambda d: d["admin"]["pin"].update(n=2**30),
    "odd scrypt cost": lambda d: d["admin"]["pin"].update(n=1000),
    "generated pin is a number": lambda d: d["admin"].update(generated_pin=123456),
}


@pytest.mark.parametrize("change", BROKEN.values(), ids=BROKEN.keys())
def test_broken_file_at_runtime_keeps_the_last_settings(tmp_path, clock, caplog, change):
    server = store(tmp_path, clock)
    server.set_volume(20, 2)
    edit(tmp_path, change)
    for _ in range(5):
        clock.now += 1.5
        assert server.current().max_volume == 20
    assert caplog.text.count("keeping the last settings") == 1  # not once a second


@pytest.mark.parametrize("change", BROKEN.values(), ids=BROKEN.keys())
def test_broken_file_at_startup_is_moved_aside(tmp_path, change):
    store(tmp_path).set_volume(20, 2)
    edit(tmp_path, change)
    st = store(tmp_path)
    assert st.load_problem == "settings_corrupt"
    assert st.current().max_volume == DEFAULT_MAX_VOLUME
    assert list(tmp_path.glob("settings.json.corrupt-*"))


def test_newer_file_at_runtime_keeps_the_last_settings(tmp_path, clock):
    server = store(tmp_path, clock)
    server.set_volume(20, 2)
    edit(tmp_path, lambda d: d.update(schema=2))
    clock.now += 1.5
    assert server.current().max_volume == 20


def test_a_change_repairs_a_broken_file(tmp_path, clock):
    server = store(tmp_path, clock)
    server.set_room("Kinderzimmer", None, None)
    (tmp_path / "settings.json").write_text("{broken")
    server.set_volume(18, 2)
    fresh = SettingsStore(tmp_path, scrypt=CHEAP)
    assert (fresh.current().room, fresh.current().max_volume) == ("Kinderzimmer", 18)
    assert list(tmp_path.glob("settings.json.corrupt-*"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_unreadable_file_at_startup_is_kept_and_stops_the_start(tmp_path):
    store(tmp_path).set_volume(20, 2)
    path = tmp_path / "settings.json"
    path.chmod(0)
    try:
        with pytest.raises(SettingsFileError, match="belongs to the user"):
            store(tmp_path)
        assert not list(tmp_path.glob("settings.json.corrupt-*"))
    finally:
        path.chmod(0o600)
    assert store(tmp_path).current().max_volume == 20


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anything")
def test_read_only_use_needs_no_write_access(tmp_path):
    store(tmp_path).set_volume(20, 2)
    (tmp_path / "settings.lock").chmod(0o400)
    tmp_path.chmod(0o500)
    try:
        assert store(tmp_path, create=False).current().max_volume == 20
    finally:
        tmp_path.chmod(0o700)


def test_pins_that_cannot_be_encoded(tmp_path):
    st = store(tmp_path)
    with pytest.raises(SettingsError) as info:
        st.change_pin("12\ud80034")
    assert info.value.code == "pin_invalid"
    assert st.check("\ud800") is None


def test_check_returns_the_version_it_matched(tmp_path):
    st = store(tmp_path)
    st.change_pin("2468")
    assert st.check("2468") == st.current().pin.version
    assert st.check("1357") is None
    assert st.check(None) is None


def test_pin_change_is_refused_if_the_pin_changed_meanwhile(tmp_path):
    st = store(tmp_path)
    st.change_pin("2468")
    checked = st.check("2468")
    store(tmp_path).reset_pin()  # e.g. reset-pin from the command line
    with pytest.raises(SettingsError) as info:
        st.change_pin("9753", expected_version=checked)
    assert info.value.code == "pin_changed"
    assert not st.verify_pin("9753")


def test_sections_of_a_newer_version_are_kept(tmp_path):
    st = store(tmp_path)
    edit(tmp_path, lambda d: d.update(future={"x": 1}))
    SettingsStore(tmp_path, scrypt=CHEAP).set_volume(20, 2)
    assert json.loads((tmp_path / "settings.json").read_text())["future"] == {"x": 1}
    assert st  # the first store is still usable


def test_time_zone(tmp_path):
    st = store(tmp_path)
    assert st.current().time_zone is None
    st.set_time_zone("Europe/Berlin")
    assert SettingsStore(tmp_path, scrypt=CHEAP).current().time_zone == "Europe/Berlin"
    with pytest.raises(SettingsError) as info:
        st.set_time_zone("Mars/Olympus")
    assert info.value.code == "time_zone_invalid"
    st.set_time_zone("")
    assert st.current().time_zone is None


def test_unknown_stored_zone_is_ignored(tmp_path):
    store(tmp_path)
    edit(tmp_path, lambda d: d.update(time={"zone": "Mars/Olympus"}))
    st = store(tmp_path)
    assert st.load_problem is None
    assert st.current().time_zone is None


# -- usage times, sleep timer, games ----------------------------------------------------

SCHEDULE = {
    "enabled": True,
    "fade_minutes": 10,
    "days": {
        "mon": {"from": "07:00", "to": "19:00"},
        "tue": {"from": "07:00", "to": "19:00"},
        "wed": None,
        "thu": None,
        "fri": None,
        "sat": {"from": "08:00", "to": "20:00"},
        "sun": None,
    },
}


def test_new_sections_have_defaults_and_round_trip(tmp_path):
    from muckebox.schedule import schedule_to_json
    from muckebox.settings import games_to_json, sleep_timer_to_json

    st = store(tmp_path)
    current = st.current()
    assert not current.schedule.enabled
    assert (current.sleep_timer.enabled, current.sleep_timer.minutes) == (False, 30)
    assert not any(current.games.enabled(game) for game in ("sound_quiz", "breathing"))
    st.set_schedule(SCHEDULE)
    st.set_sleep_timer({"enabled": True, "minutes": 45, "wake": "06:30"})
    games = {
        "daily_minutes": 20,
        "dance_tile": "t0123456789abcde",
        "items": {"sound_quiz": {"enabled": True, "level": 1}},
    }
    st.set_games(games)
    reread = SettingsStore(tmp_path, scrypt=CHEAP).current()
    assert schedule_to_json(reread.schedule) == SCHEDULE
    assert sleep_timer_to_json(reread.sleep_timer) == {
        "enabled": True,
        "minutes": 45,
        "wake": "06:30",
    }
    stored = games_to_json(reread.games)
    assert stored["items"]["sound_quiz"] == {"enabled": True, "level": 1}
    assert stored["items"]["freeze_dance"] == {"enabled": False, "level": 2}
    assert reread.games.dance_tile == "t0123456789abcde"


def test_a_file_without_the_new_sections_still_loads(tmp_path):
    store(tmp_path).set_volume(20, 2)
    edit(tmp_path, lambda d: [d.pop(key) for key in ("schedule", "sleep_timer", "games", "time")])
    st = store(tmp_path)
    assert st.load_problem is None
    assert st.current().max_volume == 20


def test_reset_pin_keeps_the_new_sections(tmp_path):
    st = store(tmp_path)
    st.set_schedule(SCHEDULE)
    SettingsStore(tmp_path, scrypt=CHEAP).reset_pin()
    assert SettingsStore(tmp_path, scrypt=CHEAP).current().schedule.enabled


@pytest.mark.parametrize(
    ("setter", "data", "code"),
    [
        (
            "set_schedule",
            {"days": {"mon": {"from": "20:00", "to": "07:00"}}},
            "schedule_order_invalid",
        ),
        ("set_schedule", {"fade_minutes": 99}, "schedule_invalid"),
        ("set_schedule", "always", "schedule_invalid"),
        ("set_sleep_timer", {"enabled": True, "minutes": 4}, "sleep_timer_invalid"),
        ("set_sleep_timer", {"minutes": 30, "wake": "7"}, "sleep_timer_invalid"),
        ("set_sleep_timer", [], "sleep_timer_invalid"),
        ("set_games", {"daily_minutes": 90}, "games_invalid"),
        ("set_games", {"dance_tile": "../x"}, "games_invalid"),
        ("set_games", {"items": {"chess": {"enabled": True}}}, "games_invalid"),
        ("set_games", {"items": {"sound_quiz": {"enabled": True, "level": 4}}}, "games_invalid"),
    ],
)
def test_invalid_new_settings(tmp_path, setter, data, code):
    with pytest.raises(SettingsError) as info:
        getattr(store(tmp_path), setter)(data)
    assert info.value.code == code
