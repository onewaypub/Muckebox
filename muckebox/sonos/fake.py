# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An in-memory Sonos system for tests and for trying the UI without a speaker.

Start Muckebox with ``MUCKEBOX_FAKE_SONOS=1`` to use it.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from . import routing
from .errors import ActionNotAvailable, SonosError, SonosUnreachable
from .model import Favorite, FavoriteRef, Playback, RoomInfo, Route, ShareLinkRef

_DIDL = (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
    '<item id="{item_id}" parentID="-1" restricted="true"><dc:title>{title}</dc:title>'
    "<upnp:class>{item_class}</upnp:class></item></DIDL-Lite>"
)

# (title, description, uri, class, art colour)
_DEMO_FAVORITES = [
    ("Kinderlieder", "Apple Music", "x-rincon-cpcontainer:1006206cplaylist%3apl.demo1?sid=204",
     "object.container.playlistContainer", (231, 111, 81)),
    ("Hörspiel Folge 1", "Apple Music", "x-rincon-cpcontainer:1004206calbum%3a1001?sid=204",
     "object.container.album.musicAlbum", (42, 157, 143)),
    ("Kinderradio", "TuneIn", "x-sonosapi-stream:s00001?sid=254&flags=8224&sn=0",
     "object.item.audioItem.audioBroadcast", (233, 196, 106)),
    ("Gute-Nacht-Musik", "Musikbibliothek", "x-rincon-playlist:RINCON_000000000000001400#S://nas/music/night",
     "object.container.playlistContainer", (38, 70, 83)),
    ("Das Hörbuch", "Audible", "x-sonos-http:book%3a42.mp4?sid=239",
     "object.item.audioItem.audioBook", (244, 162, 97)),
    ("Fernseher", "TV", "x-sonos-htastream:RINCON_000000000000001400:spdif", "", (120, 120, 120)),
]  # fmt: skip


def demo_favorites() -> list[Favorite]:
    favorites = []
    for number, (title, description, uri, item_class, _colour) in enumerate(_DEMO_FAVORITES, 1):
        route, reason = routing.classify(uri, item_class)
        ref = FavoriteRef(
            uri=uri,
            protocol_info="x-rincon-cpcontainer:*:*:*",
            res_md=_DIDL.format(item_id=f"FV:2/{number}", title=title, item_class=item_class),
            item_class=item_class,
            title=title,
        )
        favorites.append(
            Favorite(
                item_id=f"FV:2/{number}",
                title=title,
                description=description,
                art_uri=f"fake-art:{number}",
                ref=ref,
                route=route,
                reason=reason,
            )
        )
    return favorites


@dataclass
class FakeSonos:
    """Behaves like one speaker. Tests can inject failures and inspect calls."""

    room_name: str = "Kinderzimmer"
    volume: int = 10
    favorites: list[Favorite] = field(default_factory=demo_favorites)
    reachable: bool = True
    fixed: bool = False
    #: Seconds a playback start takes (lets tests exercise "pending").
    start_delay: float = 0.0
    #: If set, the next call of that method raises this error once.
    fail_next: dict[str, SonosError] = field(default_factory=dict)
    #: Called with the method name before every call (tests can block here).
    on_call: Callable[[str], None] | None = None

    calls: list[tuple] = field(default_factory=list)
    state: str = "stopped"
    media_uri: str = ""
    queue: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _enter(self, name: str, *args: object) -> None:
        if self.on_call:
            self.on_call(name)
        with self._lock:
            self.calls.append((name, *args))
            error = self.fail_next.pop(name, None)
        if error is not None:
            raise error
        if not self.reachable:
            raise SonosUnreachable("fake speaker switched off")

    def resolve(self) -> RoomInfo:
        self._enter("resolve")
        return RoomInfo(
            name=self.room_name,
            player_ip="192.0.2.10",
            coordinator_ip="192.0.2.10",
            coordinator_uid="RINCON_000000000000001400",
        )

    def list_favorites(self) -> list[Favorite]:
        self._enter("list_favorites")
        return list(self.favorites)

    def play_favorite(self, ref: FavoriteRef, route: Route) -> Route:
        self._enter("play_favorite", ref.uri, route)
        self._wait()
        if route is Route.DIRECT:
            self.media_uri, self.queue = ref.uri, []
        else:
            self.media_uri, self.queue = "x-rincon-queue:RINCON_000000000000001400#0", [ref.uri]
        self.state = "playing"
        return route

    def play_share_link(self, link: ShareLinkRef, title: str) -> None:
        self._enter("play_share_link", link, title)
        self._wait()
        self.media_uri = "x-rincon-queue:RINCON_000000000000001400#0"
        self.queue = [f"{link.service}:{link.kind}:{link.item_id}"]
        self.state = "playing"

    def transport(self, action: str) -> None:
        self._enter("transport", action)
        if action in ("next", "previous") and not self.queue:
            raise ActionNotAvailable(upnp_code=701)
        if action == "play" and self.media_uri:
            self.state = "playing"
        elif action == "pause" and self.state == "playing":
            self.state = "paused"

    def playback(self) -> Playback:
        self._enter("playback")
        actions = {"Play", "Stop", "Pause"}
        if self.queue:
            actions |= {"Next", "Previous"}
        return Playback(
            state=self.state,
            media_uri=self.media_uri,
            first_queue_uri=self.queue[0] if self.queue else None,
            actions=frozenset(actions),
        )

    def get_volume(self) -> int:
        self._enter("get_volume")
        return self.volume

    def set_volume(self, volume: int) -> None:
        self._enter("set_volume", volume)
        self.volume = max(0, min(100, volume))

    def fixed_volume(self) -> bool:
        self._enter("fixed_volume")
        return self.fixed

    def fetch_art(self, uri: str) -> bytes:
        self._enter("fetch_art", uri)
        from PIL import Image

        number = int(uri.rsplit(":", 1)[-1]) if uri.startswith("fake-art:") else 1
        colour = _DEMO_FAVORITES[(number - 1) % len(_DEMO_FAVORITES)][4]
        buffer = io.BytesIO()
        Image.new("RGB", (300, 300), colour).save(buffer, "PNG")
        return buffer.getvalue()

    def _wait(self) -> None:
        if self.start_delay:
            threading.Event().wait(self.start_delay)
