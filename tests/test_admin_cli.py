# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""python -m muckebox.admin"""

import io
import json
import os
import re

import pytest

from muckebox import admin, settings
from muckebox.settings import SettingsStore

from .conftest import TEST_SCRYPT


@pytest.fixture(autouse=True)
def cheap_hashing(monkeypatch):
    monkeypatch.setattr(settings, "SCRYPT", TEST_SCRYPT)


def run(tmp_path, *argv):
    out = io.StringIO()
    code = admin.main(list(argv or ["reset-pin"]), {"DATA_DIR": str(tmp_path)}, out)
    return code, out.getvalue()


def printed_pin(output):
    return re.search(r"PIN for the parents' page: (\d{6})", output).group(1)


def test_reset_pin_keeps_the_other_settings(tmp_path, make_store):
    make_store("Wohnzimmer", "192.0.2.11", pin="2468").set_volume(18, 2)
    code, out = run(tmp_path)
    assert code == 0
    pin = printed_pin(out)
    store = SettingsStore(tmp_path)
    assert store.verify_pin(pin)
    assert not store.verify_pin("2468")
    current = store.current()
    assert current.pin.generated == pin  # shown in the server log until changed
    assert (current.room, current.seed_ip, current.max_volume) == ("Wohnzimmer", "192.0.2.11", 18)


def test_reset_pin_before_the_first_start(tmp_path):
    code, out = run(tmp_path)
    assert code == 0
    assert SettingsStore(tmp_path).current().pin.generated == printed_pin(out)


def test_reset_pin_refuses_newer_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema": 99}))
    code, out = run(tmp_path)
    assert code == admin.EXIT_SETTINGS
    assert "newer" in out
    assert json.loads(path.read_text()) == {"schema": 99}


def test_reset_pin_with_a_corrupt_file(tmp_path):
    (tmp_path / "settings.json").write_text("{broken")
    code, out = run(tmp_path)
    assert code == 0
    assert "moved aside" in out
    assert printed_pin(out)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_reset_pin_without_write_access(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    code, out = run(locked)
    assert code == admin.EXIT_NOT_WRITABLE
    assert "Run this as the user Muckebox runs as" in out


def test_a_command_is_required(tmp_path):
    with pytest.raises(SystemExit):
        admin.main([], {"DATA_DIR": str(tmp_path)}, io.StringIO())
