# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import os
from datetime import UTC, datetime

import pytest

from muckebox.localtime import ZoneResolver, load_zone, local_time


def resolver(tmp_path, tz=None, system=None):
    link = tmp_path / "localtime"
    if system:
        os.symlink(f"/usr/share/zoneinfo/{system}", link)
    return ZoneResolver({"TZ": tz} if tz is not None else {}, localtime=link)


@pytest.mark.parametrize("name", ["Europe/Berlin", "UTC", "America/Argentina/Buenos_Aires"])
def test_known_zones(name):
    assert load_zone(name) is not None


@pytest.mark.parametrize(
    "name", ["", "Mars/Olympus", "../../etc/passwd", "/etc/localtime", "CET-1CEST", 5, None]
)
def test_unknown_zones(name):
    assert load_zone(name) is None


@pytest.mark.parametrize("tz", ["Europe/Berlin", ":Europe/Berlin", " Europe/Berlin "])
def test_tz_variable(tmp_path, tz):
    zone = resolver(tmp_path, tz=tz, system="America/New_York").zone()
    assert (zone.name, zone.source) == ("Europe/Berlin", "TZ")


def test_posix_rules_are_ignored_with_a_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    zone = resolver(tmp_path, tz="CET-1CEST,M3.5.0,M10.5.0/3", system="Europe/Vienna").zone()
    assert (zone.name, zone.source) == ("Europe/Vienna", "system")
    assert "not a time zone name" in caplog.text


def test_without_tz_the_system_zone_or_utc(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolver(tmp_path / "a", system="Europe/Berlin").zone().source == "system"
    fallback = resolver(tmp_path).zone()  # no /etc/localtime link at all
    assert (fallback.name, fallback.source) == ("UTC", "default")


def test_the_parents_choice_wins(tmp_path):
    zones = resolver(tmp_path, tz="Europe/Berlin")
    assert zones.zone("Europe/Lisbon").name == "Europe/Lisbon"
    assert zones.zone("Europe/Lisbon").source == "settings"
    assert zones.zone("Nowhere/Land").name == "Europe/Berlin"  # unknown: back to TZ
    assert zones.zone(None).name == "Europe/Berlin"


def test_local_time_follows_daylight_saving(tmp_path):
    zone = resolver(tmp_path, tz="Europe/Berlin").zone()
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=UTC).timestamp()
    winter = datetime(2026, 12, 1, 10, 0, tzinfo=UTC).timestamp()
    assert local_time(summer, zone).hour == 12
    assert local_time(winter, zone).hour == 11


def test_posix_and_right_zone_links_use_the_plain_name(tmp_path):
    link = tmp_path / "localtime"
    os.symlink("/usr/share/zoneinfo/posix/Europe/Berlin", link)
    zone = ZoneResolver({}, localtime=link).zone()
    assert (zone.name, zone.source) == ("Europe/Berlin", "system")
