# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import ipaddress
import socket

import pytest

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto
_real_sendmsg = socket.socket.sendmsg
_real_getaddrinfo = socket.getaddrinfo


def _is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode()
    if host in (None, "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check(sock, address):
    host = address[0] if isinstance(address, tuple) else None
    if not _is_loopback(host):
        if sock is not None:
            sock.close()
        raise RuntimeError(f"Tests must not open network connections (tried {address!r})")


def _connect(self, address):
    _check(self, address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _check(self, address)
    return _real_connect_ex(self, address)


def _sendto(self, data, *args):
    _check(self, args[-1])  # sendto(data[, flags], address)
    return _real_sendto(self, data, *args)


def _sendmsg(self, buffers, *args):
    if len(args) >= 3:  # sendmsg(buffers[, ancdata[, flags[, address]]])
        _check(self, args[2])
    return _real_sendmsg(self, buffers, *args)


def _getaddrinfo(host, *args, **kwargs):
    # IP literals need no DNS; connect/sendto still check them.
    literal = host
    if isinstance(literal, bytes):
        literal = literal.decode()
    try:
        ipaddress.ip_address(literal)
    except (TypeError, ValueError):
        if not _is_loopback(literal):
            raise RuntimeError(f"Tests must not resolve host names (tried {host!r})") from None
    return _real_getaddrinfo(host, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that tries to reach a real host (e.g. a Sonos speaker)."""
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", _sendto)
    monkeypatch.setattr(socket.socket, "sendmsg", _sendmsg)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
