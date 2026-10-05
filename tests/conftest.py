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


# -- application fixtures ----------------------------------------------------------


TEST_SCRYPT = {"n": 2**4, "r": 8, "p": 1}  # fast PIN hashing for tests


@pytest.fixture
def household():
    from muckebox.sonos.fake import FakeHousehold

    return FakeHousehold()


@pytest.fixture
def fake_sonos(household):
    """The speaker of the room the services control by default."""
    return household.speaker("Kinderzimmer")


@pytest.fixture
def make_store(tmp_path, household):
    """A settings store in tmp_path; ``room`` is chosen unless it is None."""
    from muckebox.settings import SettingsStore

    def make(room="Kinderzimmer", seed_ip=None, pin=None, **kwargs):
        store = SettingsStore(tmp_path, scrypt=TEST_SCRYPT, **kwargs)
        if room is not None:
            store.set_room(room, household.uid(room), seed_ip)
        if pin is not None:
            store.change_pin(pin)
        return store

    return make


@pytest.fixture
def fake_hue():
    from muckebox.hue.fake import FakeHue

    return FakeHue()


@pytest.fixture
def make_services(tmp_path, household, make_store, fake_hue):
    """Build the app's services on FakeHousehold, with lanes that run inline."""
    from muckebox.config import load_settings
    from muckebox.covers import CoverStore
    from muckebox.library import Library
    from muckebox.runtime.clock import FakeClock
    from muckebox.runtime.lanes import InlineLane
    from muckebox.runtime.service import Runtime
    from muckebox.web.app import Services

    def make(room="Kinderzimmer", pin="2468", **env):
        settings = load_settings({"DATA_DIR": str(tmp_path), **env})
        store = make_store(room, pin=pin)
        library = Library(tmp_path / "library.json")
        runtime = Runtime(
            store,
            library,
            tmp_path,
            backend_factory=household.backend,
            room_finder=household.find_rooms,
            clock=FakeClock(),
            lane_factory=lambda name, idle, interval: InlineLane(name),
            hue=fake_hue,
        )
        return Services(
            settings=settings,
            store=store,
            runtime=runtime,
            library=library,
            covers=CoverStore(tmp_path / "covers"),
            secret_key=b"k" * 32,
        )

    return make


@pytest.fixture
def services(make_services):
    return make_services()


@pytest.fixture
def app(services):
    from muckebox.web import create_app

    return create_app(services)


@pytest.fixture
def client(app):
    return app.test_client()
