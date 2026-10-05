# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A simulated Hue bridge for tests and the demo mode."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .errors import (
    HueCertificateChanged,
    HueLinkButton,
    HueNotFound,
    HueUnauthorized,
    HueUnreachable,
)
from .model import BridgeInfo, Pairing, Room, Scene

DEMO_IP = "192.0.2.50"
DEMO_ID = "001788fffe000001"
DEMO_KEY = "demo-key"
DEMO_FINGERPRINT = "ab" * 32


def _demo_rooms() -> list[Room]:
    return [
        Room("room-kids", "Kinderzimmer", "group-kids"),
        Room("room-living", "Wohnzimmer", "group-living"),
    ]


def _demo_scenes() -> list[Scene]:
    return [
        Scene("scene-bright", "Hell", "room-kids", False),
        Scene("scene-read", "Lesen", "room-kids", False),
        Scene("scene-night", "Nachtlicht", "room-kids", False),
        Scene("scene-relax", "Entspannen", "room-living", False),
    ]


@dataclass
class FakeBridge:
    """Behaves like one bridge; tests inspect ``calls`` and change the state."""

    ip: str = DEMO_IP
    id: str = DEMO_ID
    name: str = "Hue Bridge"
    key: str = DEMO_KEY
    fingerprint: str = DEMO_FINGERPRINT
    reachable: bool = True
    #: Pairing works only while the button counts as pressed.
    button_pressed: bool = True
    rooms_list: list[Room] = field(default_factory=_demo_rooms)
    scenes_list: list[Scene] = field(default_factory=_demo_scenes)
    #: Light groups that are on.
    lit: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def _enter(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        if not self.reachable:
            raise HueUnreachable(self.ip)

    def info(self) -> BridgeInfo:
        return BridgeInfo(self.ip, self.id, self.name)

    # -- what a paired client may do ------------------------------------------------

    def rooms(self) -> list[Room]:
        self._enter("rooms")
        return list(self.rooms_list)

    def scenes(self) -> list[Scene]:
        self._enter("scenes")
        with self._lock:
            return list(self.scenes_list)

    def recall(self, scene_id: str) -> None:
        self._enter("recall", scene_id)
        with self._lock:
            target = next((s for s in self.scenes_list if s.id == scene_id), None)
            if target is None:
                raise HueNotFound(scene_id)
            # One scene per room is active; others in the same room are not.
            self.scenes_list = [
                Scene(s.id, s.name, s.room, s.id == scene_id if s.room == target.room else s.active)
                for s in self.scenes_list
            ]
            room = next((r for r in self.rooms_list if r.id == target.room), None)
            if room and room.grouped_light:
                self.lit.add(room.grouped_light)

    def off(self, grouped_light: str) -> None:
        self._enter("off", grouped_light)
        with self._lock:
            room = next((r for r in self.rooms_list if r.grouped_light == grouped_light), None)
            if room is None:
                raise HueNotFound(grouped_light)
            self.lit.discard(grouped_light)
            self.scenes_list = [
                Scene(s.id, s.name, s.room, False if s.room == room.id else s.active)
                for s in self.scenes_list
            ]


class FakeClient:
    """A client of the fake bridge, checking key and certificate like the real one."""

    def __init__(self, bridge: FakeBridge, key: str, fingerprint: str) -> None:
        self.bridge = bridge
        self.key = key
        self.fingerprint = fingerprint

    def _check(self) -> FakeBridge:
        if not self.bridge.reachable:
            raise HueUnreachable(self.bridge.ip)
        if self.fingerprint != self.bridge.fingerprint:
            raise HueCertificateChanged(self.bridge.ip)
        if self.key != self.bridge.key:
            raise HueUnauthorized()
        return self.bridge

    def rooms(self) -> list[Room]:
        return self._check().rooms()

    def scenes(self) -> list[Scene]:
        return self._check().scenes()

    def recall(self, scene_id: str) -> None:
        self._check().recall(scene_id)

    def off(self, grouped_light: str) -> None:
        self._check().off(grouped_light)


class FakeHue:
    """The connector for one simulated bridge."""

    def __init__(self, bridge: FakeBridge | None = None) -> None:
        self.bridge = bridge or FakeBridge()

    def find(self, ip: str | None) -> list[BridgeInfo]:
        bridge = self.bridge
        if not bridge.reachable or (ip is not None and ip != bridge.ip):
            return []
        return [bridge.info()]

    def pair(self, ip: str) -> Pairing:
        bridge = self.bridge
        if not bridge.reachable or ip != bridge.ip:
            raise HueUnreachable(ip)
        if not bridge.button_pressed:
            raise HueLinkButton()
        return Pairing(bridge.info(), bridge.key, bridge.fingerprint)

    def fingerprint(self, ip: str) -> str:
        if not self.bridge.reachable or ip != self.bridge.ip:
            raise HueUnreachable(ip)
        return self.bridge.fingerprint

    def client(self, ip: str, key: str, fingerprint: str) -> FakeClient:
        if ip != self.bridge.ip:
            return FakeClient(FakeBridge(ip=ip, reachable=False), key, fingerprint)
        return FakeClient(self.bridge, key, fingerprint)
