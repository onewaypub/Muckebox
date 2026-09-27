# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

import pytest

from muckebox.config import (
    DEFAULT_MAX_VOLUME,
    DEFAULT_PORT,
    DEFAULT_VOLUME_STEP,
    FatalConfigError,
    load_settings,
)

BASE = {"SONOS_IP": "192.0.2.10"}


def codes(settings):
    return {p.code for p in settings.problems}


def test_defaults():
    s = load_settings(BASE)
    assert s.sonos_ip == "192.0.2.10"
    assert s.sonos_room is None
    assert s.max_volume == DEFAULT_MAX_VOLUME == 25
    assert s.volume_step == DEFAULT_VOLUME_STEP == 3
    assert s.admin_pin is None
    assert s.admin_locked
    assert s.data_dir == Path("/data")
    assert s.port == DEFAULT_PORT == 8484
    assert s.sonos_config_ok
    assert codes(s) == {"admin_pin_missing"}


def test_all_values_are_trimmed():
    s = load_settings(
        {
            "SONOS_ROOM": "  Kids room ",
            "SONOS_IP": " 192.0.2.10 ",
            "MAX_VOLUME": " 30 ",
            "VOLUME_STEP": " 5",
            "ADMIN_PIN": " 2468 ",
            "DATA_DIR": " /srv/muckebox ",
            "PORT": " 9000 ",
        }
    )
    assert s.sonos_room == "Kids room"
    assert s.sonos_ip == "192.0.2.10"
    assert (s.max_volume, s.volume_step) == (30, 5)
    assert s.admin_pin == "2468"
    assert not s.admin_locked
    assert s.data_dir == Path("/srv/muckebox")
    assert s.port == 9000
    assert s.problems == ()


def test_empty_values_count_as_unset():
    s = load_settings({"SONOS_ROOM": "Kids room", "SONOS_IP": "  ", "ADMIN_PIN": ""})
    assert s.sonos_ip is None
    assert s.admin_pin is None


def test_room_only_is_valid():
    s = load_settings({"SONOS_ROOM": "Kids room"})
    assert s.sonos_config_ok


def test_neither_room_nor_ip_degrades():
    s = load_settings({})
    assert "sonos_not_configured" in codes(s)
    assert not s.sonos_config_ok


@pytest.mark.parametrize("ip", ["192.0.2.10", "sonos-kids.home.arpa", "kids-speaker"])
def test_valid_sonos_ip_or_hostname(ip):
    assert load_settings({"SONOS_IP": ip}).sonos_config_ok


@pytest.mark.parametrize(
    "ip", ["192.0.2.300", "2001:db8::1", "http://192.0.2.10", "192.0.2.10:1400", "bad host", "-x"]
)
def test_invalid_sonos_ip_degrades(ip):
    s = load_settings({"SONOS_IP": ip})
    assert "sonos_ip_invalid" in codes(s)
    assert not s.sonos_config_ok


@pytest.mark.parametrize("value", ["0", "101", "-5", "loud", "25.5"])
def test_invalid_max_volume_degrades(value):
    s = load_settings({**BASE, "MAX_VOLUME": value})
    assert "max_volume_invalid" in codes(s)
    assert not s.sonos_config_ok
    assert s.max_volume == DEFAULT_MAX_VOLUME


@pytest.mark.parametrize("value", ["1", "100"])
def test_max_volume_bounds(value):
    assert load_settings({**BASE, "MAX_VOLUME": value}).max_volume == int(value)


@pytest.mark.parametrize(
    ("max_volume", "step"), [("25", "0"), ("25", "26"), ("25", "x"), ("10", "-1")]
)
def test_invalid_volume_step_degrades(max_volume, step):
    s = load_settings({**BASE, "MAX_VOLUME": max_volume, "VOLUME_STEP": step})
    assert "volume_step_invalid" in codes(s)
    assert not s.sonos_config_ok


def test_default_step_is_capped_by_small_max_volume():
    s = load_settings({**BASE, "MAX_VOLUME": "2"})
    assert s.volume_step == 2
    assert s.sonos_config_ok


def test_short_pin_locks_admin_with_warning():
    s = load_settings({**BASE, "ADMIN_PIN": "123"})
    assert s.admin_pin is None
    assert s.admin_locked
    assert "admin_pin_too_short" in codes(s)
    # A PIN problem never blocks playback.
    assert s.sonos_config_ok


def test_problem_details_never_contain_the_pin():
    s = load_settings({**BASE, "ADMIN_PIN": "987"})
    assert all("987" not in p.detail for p in s.problems)


@pytest.mark.parametrize(
    "port",
    ["80", "1023", "1400", "1450", "1499", "5000", "5001", "5357", "6000", "10080", "65536", "x"],
)
def test_invalid_port_is_fatal(port):
    with pytest.raises(FatalConfigError, match="PORT"):
        load_settings({**BASE, "PORT": port})


@pytest.mark.parametrize("port", ["1024", "1399", "1500", "8080", "8484", "65535"])
def test_valid_ports(port):
    assert load_settings({**BASE, "PORT": port}).port == int(port)


def test_relative_data_dir_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings({**BASE, "DATA_DIR": "data"})
    assert s.data_dir == tmp_path / "data"
    assert s.data_dir.is_absolute()


def test_settings_are_immutable():
    s = load_settings(BASE)
    with pytest.raises(AttributeError):
        s.max_volume = 100


def test_repr_hides_the_pin():
    assert "2468" not in repr(load_settings({**BASE, "ADMIN_PIN": "2468"}))


@pytest.mark.parametrize("pin", ["change-me", "CHANGE-ME", "changeme", "1234", "0000"])
def test_example_pins_lock_the_admin(pin):
    s = load_settings({**BASE, "ADMIN_PIN": pin})
    assert s.admin_locked
    assert "admin_pin_placeholder" in codes(s)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "0.0.0.0"),
        ("all", "0.0.0.0"),
        ("ALL", "0.0.0.0"),
        ("localhost", "127.0.0.1"),
        ("192.0.2.5", "192.0.2.5"),
    ],
)
def test_listen(value, expected):
    env = {**BASE, "LISTEN": value} if value is not None else BASE
    assert load_settings(env).listen == expected


@pytest.mark.parametrize(
    "value", ["everywhere", "nas.example", "192.0.2.300", "::", "::1", "fe80::1%eth0"]
)
def test_invalid_listen_is_fatal(value):
    with pytest.raises(FatalConfigError, match="LISTEN"):
        load_settings({**BASE, "LISTEN": value})
