# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The interface between Muckebox and a Sonos system.

:class:`~muckebox.sonos.soco_backend.SocoBackend` implements it with SoCo;
:class:`~muckebox.sonos.fake.FakeSonos` implements it in memory for tests
and for trying the UI without a speaker.

All methods may block on the network and raise
:class:`~muckebox.sonos.errors.SonosError`. They are called from the
runtime's worker threads, never from HTTP request handlers.
"""

from __future__ import annotations

from typing import Protocol

from .model import (
    Favorite,
    FavoriteRef,
    Playback,
    Position,
    RoomInfo,
    Route,
    ShareLinkRef,
    StartAt,
)

TRANSPORT_ACTIONS = ("play", "pause", "next", "previous")


class SonosBackend(Protocol):
    def resolve(self) -> RoomInfo:
        """Find the configured room (again) and its group coordinator."""
        ...

    def list_favorites(self) -> list[Favorite]: ...

    def play_favorite(self, ref: FavoriteRef, route: Route, start: StartAt | None = None) -> Route:
        """Start a favorite; return the route that finally worked.

        ``start`` resumes a queue at a saved track and position; it is
        ignored if that track is not where it was, and never fails the start.
        """
        ...

    def play_share_link(
        self, link: ShareLinkRef, title: str, start: StartAt | None = None
    ) -> None: ...

    def position(self) -> Position | None:
        """Track and position within the current source, if Sonos knows them."""
        ...

    def transport(self, action: str) -> None:
        """One of :data:`TRANSPORT_ACTIONS`, sent to the group coordinator."""
        ...

    def playback(self) -> Playback: ...

    def get_volume(self) -> int:
        """Volume of the configured room's own player (0-100)."""
        ...

    def set_volume(self, volume: int) -> None: ...

    def set_mute(self, muted: bool) -> None:
        """Mute or unmute the configured room's own player (not its group)."""
        ...

    def fixed_volume(self) -> bool:
        """True if the player's volume is fixed (line-out products)."""
        ...

    def fetch_art(self, uri: str) -> bytes:
        """Download album art that Sonos serves or references."""
        ...
