# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest
import requests

from muckebox.hue import client
from muckebox.hue.errors import (
    HueCertificateChanged,
    HueError,
    HueLinkButton,
    HueNotFound,
    HueUnauthorized,
    HueUnreachable,
)
from muckebox.hue.fake import FakeBridge, FakeHue

IP = "192.0.2.50"


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    """Records requests and answers from a table: (method, path) -> data or error."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def request(self, method, url, timeout=None, json=None, headers=None):
        path = url.split(IP, 1)[1]
        self.requests.append((method, path, json, headers))
        answer = self.answers[(method, path)]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Response) else Response(answer)


CONFIG = {"bridgeid": "001788FFFE123456", "name": "Hue Bridge"}


def test_pairing_returns_the_key_and_the_pinned_fingerprint():
    session = Session(
        {
            ("POST", "/api"): [{"success": {"username": "secret-key", "clientkey": "x"}}],
            ("GET", "/api/0/config"): CONFIG,
        }
    )
    pairing = client.pair(IP, "muckebox#nas", session=session, fingerprint="ab" * 32)
    assert pairing.key == "secret-key"
    assert pairing.fingerprint == "ab" * 32
    assert (pairing.info.id, pairing.info.name) == ("001788fffe123456", "Hue Bridge")
    body = session.requests[0][2]
    assert body == {"devicetype": "muckebox#nas", "generateclientkey": True}


def test_pairing_before_the_button_is_pressed():
    answer = [{"error": {"type": 101, "description": "link button not pressed"}}]
    session = Session({("POST", "/api"): answer})
    with pytest.raises(HueLinkButton):
        client.pair(IP, "muckebox#nas", session=session, fingerprint="ab" * 32)


@pytest.mark.parametrize(
    "answer",
    [[{"error": {"type": 7, "description": "invalid"}}], [{"success": {}}], {"odd": 1}, []],
)
def test_pairing_with_an_odd_answer(answer):
    session = Session({("POST", "/api"): answer})
    with pytest.raises(HueError):
        client.pair(IP, "muckebox#nas", session=session, fingerprint="ab" * 32)


def test_not_a_bridge():
    session = Session({("GET", "/api/0/config"): {"hello": "world"}})
    with pytest.raises(HueError):
        client.read_info(session, IP)


ROOMS = {
    "data": [
        {
            "id": "r1",
            "metadata": {"name": "Kinder\nzimmer"},
            "services": [{"rid": "d1", "rtype": "device"}, {"rid": "g1", "rtype": "grouped_light"}],
        },
        {"id": "r2", "metadata": {"name": "Leer"}, "services": []},
    ]
}
ZONES = {"data": [{"id": "z1", "metadata": {"name": "Ecke"}, "services": []}]}
SCENES = {
    "data": [
        {
            "id": "s1",
            "metadata": {"name": "Nachtlicht"},
            "group": {"rid": "r1", "rtype": "room"},
            "status": {"active": "static"},
        },
        {"id": "s2", "metadata": {"name": "Hell"}, "group": {"rid": "r1", "rtype": "room"}},
        {"id": "s3", "metadata": {"name": "?"}, "group": {"rid": "x", "rtype": "bridge_home"}},
        "not a dict",
    ]
}


def paired(answers):
    session = Session(answers)
    return client.HueClient(IP, "secret-key", "ab" * 32, session=session), session


def test_reading_rooms_and_scenes():
    hue, session = paired(
        {
            ("GET", "/clip/v2/resource/room"): ROOMS,
            ("GET", "/clip/v2/resource/zone"): ZONES,
            ("GET", "/clip/v2/resource/scene"): SCENES,
        }
    )
    rooms = hue.rooms()
    assert [(r.id, r.name, r.grouped_light) for r in rooms] == [
        ("r1", "Kinder zimmer", "g1"),
        ("r2", "Leer", None),
        ("z1", "Ecke", None),
    ]
    scenes = hue.scenes()
    assert [(s.id, s.room, s.active) for s in scenes] == [
        ("s1", "r1", True),
        ("s2", "r1", False),
        ("s3", None, False),
    ]
    assert all(headers == {"hue-application-key": "secret-key"} for *_, headers in session.requests)


def test_switching_sends_the_documented_bodies():
    hue, session = paired(
        {
            ("PUT", "/clip/v2/resource/scene/s1"): {"data": [{"rid": "s1"}]},
            ("PUT", "/clip/v2/resource/grouped_light/g1"): {"data": [{"rid": "g1"}]},
        }
    )
    hue.recall("s1")
    hue.off("g1")
    assert [(m, p, b) for m, p, b, _ in session.requests] == [
        ("PUT", "/clip/v2/resource/scene/s1", {"recall": {"action": "active"}}),
        ("PUT", "/clip/v2/resource/grouped_light/g1", {"on": {"on": False}}),
    ]


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (requests.exceptions.SSLError("Fingerprints did not match."), HueCertificateChanged),
        (requests.exceptions.SSLError("handshake failure"), HueUnreachable),
        (requests.exceptions.ConnectTimeout("timeout"), HueUnreachable),
        (Response({}, 403), HueUnauthorized),
        (Response({}, 404), HueNotFound),
        (Response({}, 500), HueError),
        (Response(ValueError("no json")), HueError),
    ],
)
def test_errors_are_mapped(answer, error):
    hue, _ = paired({("GET", "/clip/v2/resource/scene"): answer})
    with pytest.raises(error):
        hue.scenes()


def test_the_session_pins_the_certificate():
    session = client.pinned_session("cd" * 32)
    adapter = session.get_adapter(f"https://{IP}/api")
    assert isinstance(adapter, client.PinnedAdapter)
    assert adapter.poolmanager.connection_pool_kw["assert_fingerprint"] == "cd" * 32
    assert session.trust_env is False


def test_no_certificate_means_unreachable(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(client.socket, "create_connection", refuse)
    with pytest.raises(HueUnreachable):
        client.fingerprint_of(IP)


# -- the fake bridge ---------------------------------------------------------------------


def test_the_fake_switches_one_scene_per_room():
    hue = FakeHue()
    pairing = hue.pair(hue.bridge.ip)
    bridge = hue.client(hue.bridge.ip, pairing.key, pairing.fingerprint)
    bridge.recall("scene-night")
    bridge.recall("scene-relax")
    bridge.recall("scene-bright")
    active = {s.id for s in bridge.scenes() if s.active}
    assert active == {"scene-bright", "scene-relax"}
    bridge.off("group-kids")
    assert {s.id for s in bridge.scenes() if s.active} == {"scene-relax"}
    with pytest.raises(HueNotFound):
        bridge.recall("scene-gone")


def test_the_fake_checks_key_certificate_and_button():
    hue = FakeHue(FakeBridge(button_pressed=False))
    with pytest.raises(HueLinkButton):
        hue.pair(hue.bridge.ip)
    with pytest.raises(HueCertificateChanged):
        hue.client(hue.bridge.ip, hue.bridge.key, "00" * 32).scenes()
    with pytest.raises(HueUnauthorized):
        hue.client(hue.bridge.ip, "wrong", hue.bridge.fingerprint).scenes()
    hue.bridge.reachable = False
    with pytest.raises(HueUnreachable):
        hue.client(hue.bridge.ip, hue.bridge.key, hue.bridge.fingerprint).rooms()
    assert hue.find(None) == []
