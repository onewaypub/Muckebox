# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plain data objects exchanged with the Sonos backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Route(StrEnum):
    """How a favorite is started on the speaker."""

    DIRECT = "direct"  # play_uri: radio, line-in, audiobooks
    QUEUE = "queue"  # clear queue, add, play from queue
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class FavoriteRef:
    """Everything needed to play a Sonos favorite without browsing again.

    ``res_md`` is the DIDL-Lite metadata Sonos stores with the favorite;
    ``item_class`` is the UPnP class of the referenced item (may be empty
    when Sonos stored broken metadata).
    """

    uri: str
    protocol_info: str
    res_md: str
    item_class: str
    title: str


@dataclass(frozen=True)
class Favorite:
    """A Sonos favorite as listed on the parents' page."""

    item_id: str
    title: str
    description: str
    art_uri: str | None
    ref: FavoriteRef | None
    route: Route
    reason: str | None = None  # why it is not playable

    @property
    def playable(self) -> bool:
        return self.route is not Route.UNSUPPORTED


@dataclass(frozen=True)
class ShareLinkRef:
    """A normalised share link: which service, what kind of item, its ID."""

    service: str  # "spotify", "apple_music", "tidal", "deezer"
    kind: str  # "album", "playlist", "track", "song", "episode", "show"
    item_id: str


@dataclass(frozen=True)
class RoomInfo:
    name: str
    player_ip: str
    coordinator_ip: str
    coordinator_uid: str
    grouped: bool = False
    player_uid: str = ""


@dataclass(frozen=True)
class RoomChoice:
    """A room found in the household, offered on the parents' page."""

    name: str
    uid: str
    ip: str
    grouped: bool = False


@dataclass(frozen=True)
class Playback:
    """Snapshot of the transport state of the room's group."""

    state: str  # playing, paused, stopped, transitioning, unknown
    media_uri: str = ""
    first_queue_uri: str | None = None
    actions: frozenset[str] = field(default_factory=frozenset)

    @property
    def can_next(self) -> bool:
        return "Next" in self.actions

    @property
    def can_prev(self) -> bool:
        return "Previous" in self.actions

    @property
    def can_pause(self) -> bool:
        return "Pause" in self.actions
