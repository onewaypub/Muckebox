# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import ipaddress
import socket

import pytest

_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else None
    try:
        loopback = host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        self.close()
        raise RuntimeError(f"Tests must not open network connections (tried {address!r})")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that tries to reach a real host (e.g. a Sonos speaker)."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
