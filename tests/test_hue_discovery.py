# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.hue import discovery
from muckebox.hue.errors import HueUnreachable
from muckebox.hue.model import BridgeInfo


def ask(ip):
    if ip == "192.0.2.99":
        raise HueUnreachable(ip)
    return BridgeInfo(ip, f"id-{ip}", "Hue Bridge")


def test_mdns_finds_bridges_and_skips_what_does_not_answer():
    found = discovery.find_bridges(None, browse=lambda: ["192.0.2.50", "192.0.2.99"], ask=ask)
    assert [b.ip for b in found] == ["192.0.2.50"]


def test_an_entered_address_is_asked_directly():
    def no_mdns():
        raise AssertionError("no search when the address is known")

    assert discovery.find_bridges("192.0.2.50", browse=no_mdns, ask=ask)[0].id == "id-192.0.2.50"
    with pytest.raises(HueUnreachable):
        discovery.find_bridges("192.0.2.99", browse=no_mdns, ask=ask)


def test_nothing_found():
    assert discovery.find_bridges(None, browse=list, ask=ask) == []
