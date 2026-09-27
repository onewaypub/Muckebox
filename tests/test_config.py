# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

import pytest

from muckebox.config import DEFAULT_PORT, LEGACY_VARIABLES, FatalConfigError, load_settings

BASE: dict[str, str] = {}


def test_defaults():
    s = load_settings({})
    assert s.data_dir == Path("/data")
    assert s.port == DEFAULT_PORT == 8484
    assert s.listen == "0.0.0.0"
    assert s.legacy == ()


def test_all_values_are_trimmed():
    s = load_settings({"DATA_DIR": " /srv/muckebox ", "PORT": " 9000 ", "LISTEN": " localhost "})
    assert s.data_dir == Path("/srv/muckebox")
    assert s.port == 9000
    assert s.listen == "127.0.0.1"


def test_old_variables_are_only_named():
    env = {name: "some value" for name in LEGACY_VARIABLES}
    env["VOLUME_STEP"] = "  "  # empty counts as unset
    s = load_settings(env)
    assert s.legacy == ("SONOS_ROOM", "SONOS_IP", "MAX_VOLUME", "ADMIN_PIN")
    assert "some value" not in repr(s)


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
        s.port = 9000


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
