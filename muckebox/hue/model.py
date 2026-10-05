# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Muckebox knows about a Hue bridge, its rooms and scenes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeInfo:
    ip: str
    id: str  # the bridge id, e.g. "001788fffe123456"
    name: str


@dataclass(frozen=True)
class Room:
    """A room or zone of the Hue app."""

    id: str
    name: str
    #: The light group that switches the whole room (None if it has no lights).
    grouped_light: str | None


@dataclass(frozen=True)
class Scene:
    id: str
    name: str
    room: str | None  # the room or zone the scene belongs to
    active: bool


@dataclass(frozen=True)
class Pairing:
    """The result of pressing the bridge's button: what Muckebox keeps."""

    info: BridgeInfo
    key: str  # the application key ("username"); a secret
    fingerprint: str  # SHA-256 of the bridge's certificate, hex
