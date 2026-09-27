# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real Sonos backend, built on SoCo.

Commands are sent as raw UPnP service calls with explicit timeouts, so that
no call can block longer than intended (several SoCo convenience methods have
no timeout of their own).
"""

from __future__ import annotations

import copy
import logging
import socket
import threading
from collections.abc import Callable, Iterable
from typing import Any
from xml.sax.saxutils import escape

import requests
import soco
from soco import config as soco_config
from soco.data_structures import DidlObject, DidlResource, to_didl_string
from soco.data_structures_entry import from_didl_string
from soco.exceptions import (
    DIDLMetadataError,
    SoCoException,
    SoCoSlaveException,
    SoCoUPnPException,
)
from soco.plugins.sharelink import ShareLinkPlugin

from . import routing
from .errors import (
    ROUTE_ERRORS,
    UPNP_SERVICE_ERROR,
    UPNP_SERVICE_ERROR_2,
    ActionNotAvailable,
    CommandRejected,
    GroupProblem,
    NotPlayable,
    PlaybackFailed,
    RoomNotFound,
    ServiceUnavailable,
    SonosError,
    SonosTimeout,
    SonosUnreachable,
    UpnpDisabled,
)
from .model import Favorite, FavoriteRef, Playback, RoomInfo, Route, ShareLinkRef
from .sharelink import plugin_uri

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 3.0  # normal commands
SLOW_TIMEOUT = 30.0  # enqueueing a large playlist can take this long
VOLUME_TIMEOUT = 1.5  # the volume guard must never wait long
DISCOVERY_TIMEOUT = 3.0
MAX_ART_BYTES = 10 * 1024 * 1024

_STATES = {
    "PLAYING": "playing",
    "PAUSED_PLAYBACK": "paused",
    "STOPPED": "stopped",
    "TRANSITIONING": "transitioning",
}
_NOT_AVAILABLE = frozenset({701, 711, 712})
_INSTANCE = ("InstanceID", 0)

_DIRECT_META = (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
    '<item id="R:0/0/0" parentID="R:0/0" restricted="true"><dc:title>{title}</dc:title>'
    "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class></item></DIDL-Lite>"
)


def configure_soco() -> None:
    """Global SoCo settings: short timeouts, no event-based fallbacks."""
    soco_config.REQUEST_TIMEOUT = REQUEST_TIMEOUT
    soco_config.ZGT_EVENT_FALLBACK = False
    # SoCo's debug log contains full SOAP bodies; keep it quiet.
    logging.getLogger("soco").setLevel(logging.WARNING)


def translate(exc: BaseException) -> SonosError:
    """Map any exception from SoCo or requests to a :class:`SonosError`."""
    if isinstance(exc, SonosError):
        return exc
    if isinstance(exc, SoCoUPnPException):
        code = _int_or_none(exc.error_code)
        if code in _NOT_AVAILABLE:
            return ActionNotAvailable(str(exc), upnp_code=code)
        if code in (UPNP_SERVICE_ERROR, UPNP_SERVICE_ERROR_2):
            return ServiceUnavailable(str(exc), upnp_code=code)
        return CommandRejected(str(exc), upnp_code=code)
    if isinstance(exc, SoCoSlaveException):
        return GroupProblem(str(exc))
    if isinstance(exc, DIDLMetadataError):
        return NotPlayable(str(exc))
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 403:
            return UpnpDisabled(str(exc))
        return CommandRejected(str(exc))
    if isinstance(exc, requests.exceptions.ConnectTimeout | requests.exceptions.ConnectionError):
        return SonosUnreachable(str(exc))
    if isinstance(exc, requests.exceptions.Timeout):
        return SonosTimeout(str(exc))
    if isinstance(exc, SoCoException):
        return CommandRejected(str(exc))
    if isinstance(exc, OSError):
        return SonosUnreachable(str(exc))
    raise exc


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _same_room(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


class SocoBackend:
    """Controls one room of a Sonos household through SoCo."""

    def __init__(
        self,
        *,
        room: str | None,
        seed_ip: str | None,
        soco_factory: Callable[[str], Any] = soco.SoCo,
        discover: Callable[..., Iterable[Any] | None] = soco.discover,
        resolve_host: Callable[[str], str] = socket.gethostbyname,
        http_get: Callable[..., requests.Response] = requests.get,
    ) -> None:
        if not room and not seed_ip:
            raise ValueError("room or seed_ip is required")
        self._room_name = room
        self._seed_ip = seed_ip
        self._soco_factory = soco_factory
        self._discover = discover
        self._resolve_host = resolve_host
        self._http_get = http_get
        self._lock = threading.RLock()
        self._player: Any = None

    # -- finding the room -------------------------------------------------

    def resolve(self) -> RoomInfo:
        with self._lock:
            try:
                player = self._find_player()
                coordinator = self._coordinator_of(player)
                group = player.group
                info = RoomInfo(
                    name=player.player_name,
                    player_ip=player.ip_address,
                    coordinator_ip=coordinator.ip_address,
                    coordinator_uid=coordinator.uid,
                    grouped=bool(group and len([m for m in group.members if m.is_visible]) > 1),
                )
            except Exception as exc:
                self._player = None
                raise translate(exc) from exc
            self._player = player
            return info

    def _find_player(self) -> Any:
        if self._seed_ip:
            ip = self._resolve_host(self._seed_ip)
            seed = self._soco_factory(ip)
            zones = seed.visible_zones
            wanted = self._room_name or seed.player_name
        else:
            zones = self._discover(timeout=DISCOVERY_TIMEOUT) or set()
            wanted = self._room_name or ""
        matches = [zone for zone in zones if _same_room(zone.player_name, wanted)]
        if not matches:
            raise RoomNotFound(f"room {wanted!r} not found")
        return sorted(matches, key=lambda zone: zone.ip_address)[0]

    @staticmethod
    def _coordinator_of(player: Any) -> Any:
        group = player.group
        coordinator = group.coordinator if group is not None else None
        if coordinator is None:
            raise GroupProblem("the room has no group coordinator")
        return coordinator

    def _room_player(self) -> Any:
        player = self._player
        if player is None:
            self.resolve()
            player = self._player
        return player

    def _coordinator(self) -> Any:
        try:
            return self._coordinator_of(self._room_player())
        except Exception as exc:
            raise translate(exc) from exc

    # -- favorites --------------------------------------------------------

    def list_favorites(self) -> list[Favorite]:
        player = self._room_player()
        try:
            items = player.music_library.get_sonos_favorites(complete_result=True)
        except Exception as exc:
            raise translate(exc) from exc
        return [self._favorite(item, player.ip_address) for item in items]

    def _favorite(self, item: Any, player_ip: str) -> Favorite:
        resources = getattr(item, "resources", None) or []
        uri = resources[0].uri if resources and resources[0].uri else ""
        protocol_info = resources[0].protocol_info if resources else ""
        res_md = getattr(item, "resource_meta_data", None) or ""
        item_class, broken = "", False
        try:
            item_class = item.reference.item_class
        except (SoCoException, AttributeError, IndexError):
            broken = True
        route, reason = routing.classify(uri, item_class)
        if route is not Route.UNSUPPORTED and broken and routing.music_source(uri) != "LIBRARY":
            route, reason = Route.UNSUPPORTED, "broken_metadata"
        title = getattr(item, "title", "") or ""
        art = getattr(item, "album_art_uri", None) or None
        if art and art.startswith("/"):
            art = f"http://{player_ip}:1400{art}"
        ref = None
        if uri:
            ref = FavoriteRef(
                uri=uri,
                protocol_info=protocol_info or "",
                res_md=res_md,
                item_class=item_class,
                title=title,
            )
        return Favorite(
            item_id=getattr(item, "item_id", "") or "",
            title=title,
            description=getattr(item, "description", "") or "",
            art_uri=art,
            ref=ref,
            route=route,
            reason=reason,
        )

    # -- playback ---------------------------------------------------------

    def play_favorite(self, ref: FavoriteRef, route: Route) -> Route:
        if route is Route.UNSUPPORTED:
            raise NotPlayable("favorite is not playable")
        coordinator = self._coordinator()
        self._normal_play_mode(coordinator)
        try:
            self._start(coordinator, ref, route)
            return route
        except SonosError as exc:
            if exc.upnp_code not in ROUTE_ERRORS:
                raise
            fallback = routing.other_route(route)
            log.info("Route %s rejected (UPnP %s); trying %s", route, exc.upnp_code, fallback)
            self._start(coordinator, ref, fallback)
            return fallback

    def _start(self, coordinator: Any, ref: FavoriteRef, route: Route) -> None:
        if route is Route.DIRECT:
            meta = ref.res_md or _DIRECT_META.format(title=escape(ref.title))
            self._call(
                coordinator.avTransport.SetAVTransportURI,
                [_INSTANCE, ("CurrentURI", ref.uri), ("CurrentURIMetaData", meta)],
                timeout=SLOW_TIMEOUT,
            )
            self._play(coordinator)
        else:
            item = self._queue_item(ref)
            self._clear_queue(coordinator)
            first = self._enqueue(
                coordinator,
                lambda: coordinator.avTransport.AddURIToQueue(
                    [
                        _INSTANCE,
                        ("EnqueuedURI", ref.uri),
                        ("EnqueuedURIMetaData", to_didl_string(item)),
                        ("DesiredFirstTrackNumberEnqueued", 0),
                        ("EnqueueAsNext", 0),
                    ],
                    timeout=SLOW_TIMEOUT,
                ),
            )
            self._play_queue(coordinator, first)

    @staticmethod
    def _queue_item(ref: FavoriteRef) -> Any:
        resource = DidlResource(uri=ref.uri, protocol_info=ref.protocol_info or "")
        item = None
        if ref.res_md:
            try:
                parsed = from_didl_string(ref.res_md)
                # from_didl_string is cached; never modify the shared objects.
                item = copy.deepcopy(parsed[0]) if parsed else None
            except (SoCoException, IndexError, ValueError, SyntaxError):
                item = None
        if item is None:
            if routing.music_source(ref.uri) != "LIBRARY":
                raise NotPlayable("favorite metadata is broken")
            # Library content needs no service metadata; a bare item works.
            item = DidlObject(resources=[resource], title=ref.title, parent_id="", item_id="")
        item.resources = [resource]
        return item

    def play_share_link(self, link: ShareLinkRef, title: str) -> None:
        coordinator = self._coordinator()
        self._normal_play_mode(coordinator)
        self._clear_queue(coordinator)
        plugin = ShareLinkPlugin(coordinator)
        first = self._enqueue(
            coordinator,
            lambda: plugin.add_share_link_to_queue(
                plugin_uri(link), dc_title=escape(title), timeout=SLOW_TIMEOUT
            ),
        )
        self._play_queue(coordinator, first)

    def _enqueue(self, coordinator: Any, add: Callable[[], Any]) -> int:
        """Run an enqueue call; tolerate a read timeout if items arrived."""
        try:
            result = add()
        except requests.exceptions.ReadTimeout as exc:
            # Sonos may still have filled the queue; play it if so.
            if self._queue_size(coordinator):
                return 1
            raise SonosTimeout("enqueueing took too long") from exc
        except Exception as exc:
            raise translate(exc) from exc
        if isinstance(result, dict):
            return _int_or_none(result.get("FirstTrackNumberEnqueued")) or 1
        return _int_or_none(result) or 1

    def _queue_size(self, coordinator: Any) -> int:
        try:
            result = coordinator.contentDirectory.Browse(
                [
                    ("ObjectID", "Q:0"),
                    ("BrowseFlag", "BrowseDirectChildren"),
                    ("Filter", "*"),
                    ("StartingIndex", 0),
                    ("RequestedCount", 1),
                    ("SortCriteria", ""),
                ],
                timeout=REQUEST_TIMEOUT,
            )
            return _int_or_none(result.get("TotalMatches")) or 0
        except Exception:
            return 0

    def _clear_queue(self, coordinator: Any) -> None:
        try:
            self._call(coordinator.avTransport.RemoveAllTracksFromQueue, [_INSTANCE])
        except SonosError as exc:
            # 804 here means "queue already empty" on some firmware.
            if exc.upnp_code != UPNP_SERVICE_ERROR_2:
                raise

    def _play_queue(self, coordinator: Any, first_track: int) -> None:
        self._call(
            coordinator.avTransport.SetAVTransportURI,
            [
                _INSTANCE,
                ("CurrentURI", f"x-rincon-queue:{coordinator.uid}#0"),
                ("CurrentURIMetaData", ""),
            ],
        )
        self._call(
            coordinator.avTransport.Seek,
            [_INSTANCE, ("Unit", "TRACK_NR"), ("Target", max(first_track, 1))],
        )
        self._play(coordinator)

    def _play(self, coordinator: Any) -> None:
        try:
            self._call(coordinator.avTransport.Play, [_INSTANCE, ("Speed", 1)])
        except ActionNotAvailable as exc:
            raise PlaybackFailed("the speaker refused to start playback") from exc

    def _normal_play_mode(self, coordinator: Any) -> None:
        """Switch shuffle and repeat off, so that audio plays keep their order."""
        try:
            self._call(coordinator.avTransport.SetPlayMode, [_INSTANCE, ("NewPlayMode", "NORMAL")])
        except SonosError as exc:
            if exc.connection_problem:
                raise
            log.debug("Could not reset play mode: %s", exc)

    def transport(self, action: str) -> None:
        coordinator = self._coordinator()
        service = coordinator.avTransport
        if action == "play":
            self._call(service.Play, [_INSTANCE, ("Speed", 1)])
        elif action == "pause":
            actions = self._actions(coordinator)
            method = service.Pause if "Pause" in actions else service.Stop
            self._call(method, [_INSTANCE, ("Speed", 1)])
        elif action == "next":
            self._call(service.Next, [_INSTANCE, ("Speed", 1)])
        elif action == "previous":
            self._call(service.Previous, [_INSTANCE, ("Speed", 1)])
        else:
            raise ValueError(f"unknown transport action {action!r}")

    def playback(self) -> Playback:
        coordinator = self._coordinator()
        info = self._call(coordinator.avTransport.GetTransportInfo, [_INSTANCE])
        media = self._call(coordinator.avTransport.GetMediaInfo, [_INSTANCE])
        media_uri = media.get("CurrentURI") or ""
        first_queue_uri = None
        if media_uri.startswith("x-rincon-queue:"):
            first_queue_uri = self._first_queue_uri(coordinator)
        return Playback(
            state=_STATES.get(info.get("CurrentTransportState", ""), "unknown"),
            media_uri=media_uri,
            first_queue_uri=first_queue_uri,
            actions=frozenset(self._actions(coordinator)),
        )

    def _actions(self, coordinator: Any) -> set[str]:
        result = self._call(coordinator.avTransport.GetCurrentTransportActions, [_INSTANCE])
        return {action.split("_")[-1] for action in result.get("Actions", "").split(", ") if action}

    def _first_queue_uri(self, coordinator: Any) -> str | None:
        result = self._call(
            coordinator.contentDirectory.Browse,
            [
                ("ObjectID", "Q:0"),
                ("BrowseFlag", "BrowseDirectChildren"),
                ("Filter", "*"),
                ("StartingIndex", 0),
                ("RequestedCount", 1),
                ("SortCriteria", ""),
            ],
        )
        try:
            items = from_didl_string(result.get("Result", ""))
        except (SoCoException, ValueError, SyntaxError):
            return None
        if items and items[0].resources:
            return items[0].resources[0].uri
        return None

    # -- volume -----------------------------------------------------------

    def get_volume(self) -> int:
        player = self._room_player()
        result = self._call(
            player.renderingControl.GetVolume,
            [_INSTANCE, ("Channel", "Master")],
            timeout=VOLUME_TIMEOUT,
        )
        return int(result["CurrentVolume"])

    def set_volume(self, volume: int) -> None:
        player = self._room_player()
        self._call(
            player.renderingControl.SetVolume,
            [_INSTANCE, ("Channel", "Master"), ("DesiredVolume", max(0, min(100, volume)))],
            timeout=VOLUME_TIMEOUT,
        )

    def fixed_volume(self) -> bool:
        player = self._room_player()
        try:
            supported = self._call(player.renderingControl.GetSupportsOutputFixed, [_INSTANCE])
            if supported.get("CurrentSupportsFixed") != "1":
                return False
            fixed = self._call(player.renderingControl.GetOutputFixed, [_INSTANCE])
        except (CommandRejected, ActionNotAvailable):
            return False
        return fixed.get("CurrentFixed") == "1"

    # -- album art --------------------------------------------------------

    def fetch_art(self, uri: str) -> bytes:
        if uri.startswith("/"):
            uri = f"http://{self._room_player().ip_address}:1400{uri}"
        if not uri.startswith(("http://", "https://")):
            raise NotPlayable("unsupported album art URI")
        try:
            response = self._http_get(uri, timeout=(REQUEST_TIMEOUT, 8), stream=True)
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data += chunk
                if len(data) > MAX_ART_BYTES:
                    raise CommandRejected("album art too large")
            return bytes(data)
        except SonosError:
            raise
        except Exception as exc:
            raise translate(exc) from exc

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _call(method: Callable[..., Any], args: list, timeout: float = REQUEST_TIMEOUT) -> Any:
        try:
            return method(args, timeout=timeout)
        except Exception as exc:
            raise translate(exc) from exc
