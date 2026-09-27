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

from .model import Favorite, FavoriteRef, Playback, RoomInfo, Route, ShareLinkRef

TRANSPORT_ACTIONS = ("play", "pause", "next", "previous")


class SonosBackend(Protocol):
    def resolve(self) -> RoomInfo:
        """Find the configured room (again) and its group coordinator."""
        ...

    def list_favorites(self) -> list[Favorite]: ...

    def play_favorite(self, ref: FavoriteRef, route: Route) -> Route:
        """Start a favorite; return the route that finally worked."""
        ...

    def play_share_link(self, link: ShareLinkRef, title: str) -> None: ...

    def transport(self, action: str) -> None:
        """One of :data:`TRANSPORT_ACTIONS`, sent to the group coordinator."""
        ...

    def playback(self) -> Playback: ...

    def get_volume(self) -> int:
        """Volume of the configured room's own player (0-100)."""
        ...

    def set_volume(self, volume: int) -> None: ...

    def fixed_volume(self) -> bool:
        """True if the player's volume is fixed (line-out products)."""
        ...

    def fetch_art(self, uri: str) -> bytes:
        """Download album art that Sonos serves or references."""
        ...


class UnconfiguredBackend:
    """Stands in when the Sonos settings are invalid; every call fails."""

    def __getattr__(self, name: str):
        from .errors import RoomNotFound

        def fail(*args: object, **kwargs: object):
            raise RoomNotFound("Sonos is not configured")

        return fail
