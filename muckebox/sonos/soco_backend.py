# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The real Sonos backend, built on SoCo.

Commands are sent as raw UPnP service calls with explicit timeouts, so that
no call can block longer than intended (several SoCo convenience methods have
no timeout of their own).
"""

from __future__ import annotations

import copy
import html
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

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

from muckebox.netfetch import ANY_HOST, Fetcher, FetchError

from . import routing
from .backend import TRANSPORT_ACTIONS
from .errors import (
    ROUTE_ERRORS,
    UPNP_SERVICE_ERROR,
    UPNP_SERVICE_ERROR_2,
    UPNP_TRANSITION_NOT_AVAILABLE,
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
from .model import (
    Favorite,
    FavoriteRef,
    Playback,
    Position,
    RoomChoice,
    RoomInfo,
    Route,
    ShareLinkRef,
    StartAt,
)
from .sharelink import plugin_uri
from .topology import Member, parse_zone_group_state

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 3.0  # normal commands
SLOW_TIMEOUT = 30.0
#: Resuming starts this many seconds before the saved position ...
RESUME_REWIND = 5
#: ... and positions shorter than this start at the beginning of the track.
RESUME_MIN_SECONDS = 10
#: How long to wait for playback before seeking a second time.
SEEK_WAIT = 3.0  # enqueueing a large playlist can take this long
VOLUME_TIMEOUT = 1.5  # the volume guard must never wait long
DISCOVERY_TIMEOUT = 3.0
SCAN_THREADS = 64  # network scan: gentle on small NAS models (about 2 s per /24)
TOPOLOGY_TTL = 5.0  # seconds a group coordinator lookup stays valid
ART_DEADLINE = 10.0  # seconds for one album art download
RETRY_PAUSE = 0.5  # seconds before repeating a step after a transient UPnP error
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


def _xml_text(text: str) -> str:
    """Escape text for an XML element (&, < and >)."""
    return html.escape(text, quote=False)


def _seconds(value: Any) -> int | None:
    """ "H:MM:SS" -> seconds; None for "NOT_IMPLEMENTED" and the like."""
    parts = str(value or "").split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


def _without_query(uri: str) -> str:
    return uri.split("?", 1)[0]


#: Longest track title passed on to the kids view.
MAX_TRACK_TITLE = 120


def _track_title(meta: Any) -> str | None:
    """The title from a track's DIDL-Lite metadata, or None."""
    if not isinstance(meta, str) or not meta.lstrip().startswith("<"):
        return None  # empty or "NOT_IMPLEMENTED"
    try:
        items = from_didl_string(meta)
    except (SoCoException, ValueError, SyntaxError, AttributeError):
        return None
    title = getattr(items[0], "title", None) if items else None
    if not isinstance(title, str):
        return None
    cleaned = " ".join("".join(c if c.isprintable() else " " for c in title).split())
    return cleaned[:MAX_TRACK_TITLE] or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _body_chunks(response: Any) -> Iterator[bytes]:
    """The body as it arrives (read1 returns after one receive)."""
    read1 = getattr(getattr(response, "raw", None), "read1", None)
    if read1 is None:
        yield from response.iter_content(64 * 1024)
        return
    while chunk := read1(64 * 1024, decode_content=True):
        yield chunk


def _same_room(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


class Household:
    """Reads the household's topology: from a known speaker, the seed or discovery."""

    def __init__(
        self,
        seed_ip: str | None,
        *,
        soco_factory: Callable[[str], Any] = soco.SoCo,
        discover: Callable[..., Iterable[Any] | None] = soco.discover,
        resolve_host: Callable[[str], str] = socket.gethostbyname,
    ) -> None:
        self.seed_ip = seed_ip
        self._soco_factory = soco_factory
        self._discover = discover
        self._resolve_host = resolve_host

    def read(self, known_ip: str | None = None) -> list[Member]:
        """Ask ``known_ip`` first, then the seed (or discovery)."""
        sources = [known_ip] if known_ip else []
        if self.seed_ip:
            try:
                sources.append(self._resolve_host(self.seed_ip))
            except OSError as exc:
                if not sources:
                    raise SonosUnreachable(f"cannot resolve {self.seed_ip}: {exc}") from exc
        elif not sources:
            # Multicast discovery first; if nothing answers (e.g. a firewall
            # drops the replies), probe the local networks on TCP 1400.
            zones = (
                self._discover(
                    timeout=DISCOVERY_TIMEOUT, allow_network_scan=True, max_threads=SCAN_THREADS
                )
                or set()
            )
            sources = sorted(zone.ip_address for zone in zones)
            if not sources:
                raise RoomNotFound("no Sonos speaker found on this network")
        error: SonosError | None = None
        for ip in dict.fromkeys(sources):
            try:
                return self._topology_from(ip)
            except SonosError as exc:
                if not exc.connection_problem:
                    raise
                error = exc
        raise error or SonosUnreachable("no speaker to ask")

    def _topology_from(self, ip: str) -> list[Member]:
        speaker = self._soco_factory(ip)
        try:
            # An explicit (empty) argument list keeps SoCo from downloading
            # the service description first, which has its own long timeout.
            result = speaker.zoneGroupTopology.GetZoneGroupState([], timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            raise translate(exc) from exc
        try:
            return parse_zone_group_state(result["ZoneGroupState"])
        except (KeyError, SyntaxError, ValueError) as exc:
            raise CommandRejected(f"unreadable zone group state: {exc}") from exc


def find_rooms(seed_ip: str | None = None, **household: Any) -> list[RoomChoice]:
    """The household's rooms (for the parents' page), without choosing one."""
    try:
        members = Household(seed_ip, **household).read()
    except SonosError:
        raise
    except Exception as exc:
        raise translate(exc) from exc
    groups: dict[str, int] = {}
    for member in members:
        if member.visible:
            groups[member.coordinator_uid] = groups.get(member.coordinator_uid, 0) + 1
    rooms: dict[str, RoomChoice] = {}
    for member in sorted((m for m in members if m.visible), key=lambda m: m.ip):
        rooms.setdefault(
            member.name,
            RoomChoice(
                name=member.name,
                uid=member.uid,
                ip=member.ip,
                grouped=groups.get(member.coordinator_uid, 0) > 1,
            ),
        )
    return sorted(rooms.values(), key=lambda room: room.name.casefold())


class SocoBackend:
    """Controls one room of a Sonos household through SoCo."""

    def __init__(
        self,
        *,
        room: str,
        room_uid: str | None = None,
        seed_ip: str | None = None,
        soco_factory: Callable[[str], Any] = soco.SoCo,
        discover: Callable[..., Iterable[Any] | None] = soco.discover,
        resolve_host: Callable[[str], str] = socket.gethostbyname,
        http_get: Callable[..., requests.Response] = requests.get,
        fetcher_factory: Callable[[], Fetcher] = Fetcher,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not room:
            raise ValueError("a room is required")
        self._room_name = room
        self._room_uid = room_uid
        self._household = Household(
            seed_ip, soco_factory=soco_factory, discover=discover, resolve_host=resolve_host
        )
        self._soco_factory = soco_factory
        self._http_get = http_get
        self._fetcher_factory = fetcher_factory
        self._clock = clock
        self._lock = threading.RLock()
        # The last room found. Kept when a later lookup fails, so that the
        # volume guard keeps working even while the seed speaker is off.
        self._room: Member | None = None
        self._coordinator_member: Member | None = None
        self._known_ips: frozenset[str] = frozenset()
        self._topology_at = 0.0
        self._player: Any = None
        self._group_size = 1

    # -- finding the room -------------------------------------------------

    def resolve(self) -> RoomInfo:
        with self._lock:
            try:
                members = self._read_topology()
                room, coordinator = self._select(members)
            except Exception as exc:
                raise translate(exc) from exc
            self._room, self._coordinator_member = room, coordinator
            self._known_ips = frozenset(member.ip for member in members)
            self._topology_at = self._clock()
            self._player = self._soco_factory(room.ip)
            group_size = sum(
                1 for m in members if m.visible and m.coordinator_uid == room.coordinator_uid
            )
            self._group_size = group_size
            return RoomInfo(
                name=room.name,
                player_ip=room.ip,
                coordinator_ip=coordinator.ip,
                coordinator_uid=coordinator.uid,
                grouped=group_size > 1,
                player_uid=room.uid,
            )

    def _read_topology(self) -> list[Member]:
        return self._household.read(known_ip=self._room.ip if self._room else None)

    def _select(self, members: list[Member]) -> tuple[Member, Member]:
        """Find the room by its speaker ID first, then by name."""
        visible = [m for m in members if m.visible]
        room = next((m for m in visible if self._room_uid and m.uid == self._room_uid), None)
        if room is None:
            by_name = sorted(
                (m for m in visible if _same_room(m.name, self._room_name)), key=lambda m: m.ip
            )
            room = by_name[0] if by_name else None
        if room is None:
            names = sorted({m.name for m in visible})
            raise RoomNotFound(
                f"room {self._room_name!r} not found; rooms: {', '.join(names) or 'none'}"
            )
        if room.name != self._room_name:
            log.info("Room %r is now called %r in the Sonos app", self._room_name, room.name)
            self._room_name = room.name
        self._room_uid = room.uid
        coordinator = next((m for m in members if m.uid == room.coordinator_uid), None)
        if coordinator is None:
            raise GroupProblem("the room has no group coordinator")
        return room, coordinator

    def _room_player(self) -> Any:
        player = self._player
        if player is None:
            self.resolve()
            player = self._player
        return player

    def _play_alone(self) -> None:
        """Take the kids room out of its group before the kids control it.

        The tablet only ever controls the kids room: the other rooms of a
        group (e.g. the living room) play on undisturbed. Looked up afresh,
        because adults may have grouped the rooms a moment ago.
        """
        self.resolve()
        if self._group_size <= 1:
            return
        player = self._room_player()
        self._call(player.avTransport.BecomeCoordinatorOfStandaloneGroup, [_INSTANCE])
        log.info("The kids room left its group; the other rooms play on")
        with self._lock:
            # The room is its own coordinator now; the next lookup confirms it.
            self._coordinator_member = self._room
            self._group_size = 1
            self._topology_at = self._clock()

    def _coordinator(self) -> tuple[Any, str]:
        """The group coordinator (as a SoCo object) and its UID, a few seconds fresh."""
        if self._room is None or self._clock() - self._topology_at > TOPOLOGY_TTL:
            self.resolve()
        coordinator = self._coordinator_member
        if coordinator is None:  # pragma: no cover - resolve() sets it or raises
            raise GroupProblem("the room has no group coordinator")
        return self._soco_factory(coordinator.ip), coordinator.uid

    # -- favorites --------------------------------------------------------

    def list_favorites(self) -> list[Favorite]:
        player = self._room_player()
        try:
            items = player.music_library.get_sonos_favorites(complete_result=True)
        except Exception as exc:
            raise translate(exc) from exc
        favorites = []
        for item in items:
            try:
                favorites.append(self._favorite(item, player.ip_address))
            except Exception:
                # One odd favorite must never hide all the others.
                log.warning("Skipping a favorite Muckebox cannot read", exc_info=True)
        return favorites

    def _favorite(self, item: Any, player_ip: str) -> Favorite:
        resources = getattr(item, "resources", None) or []
        uri = resources[0].uri if resources and resources[0].uri else ""
        protocol_info = resources[0].protocol_info if resources else ""
        res_md = getattr(item, "resource_meta_data", None) or ""
        item_class, broken = "", False
        try:
            item_class = item.reference.item_class
        except Exception:  # resMD comes from the speaker: missing, empty or unparsable
            broken, res_md = True, ""
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

    def play_favorite(self, ref: FavoriteRef, route: Route, start: StartAt | None = None) -> Route:
        if route is Route.UNSUPPORTED:
            raise NotPlayable("favorite is not playable")
        self._play_alone()
        coordinator, uid = self._coordinator()
        try:
            self._start(coordinator, uid, ref, route, start)
            return route
        except SonosError as exc:
            if exc.upnp_code not in ROUTE_ERRORS:
                raise
            fallback = routing.other_route(route)
            log.info("Route %s rejected (UPnP %s); trying %s", route, exc.upnp_code, fallback)
            self._start(coordinator, uid, ref, fallback)
            return fallback

    def _start(
        self,
        coordinator: Any,
        uid: str,
        ref: FavoriteRef,
        route: Route,
        start: StartAt | None = None,
    ) -> None:
        if route is Route.DIRECT:
            meta = ref.res_md or _DIRECT_META.format(title=_xml_text(ref.title))
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
            self._play_queue(coordinator, uid, first, start)

    @staticmethod
    def _queue_item(ref: FavoriteRef) -> Any:
        resource = DidlResource(uri=ref.uri, protocol_info=ref.protocol_info or "")
        item = None
        if ref.res_md:
            try:
                parsed = from_didl_string(ref.res_md)
                # from_didl_string is cached; never modify the shared objects.
                item = copy.deepcopy(parsed[0]) if parsed else None
            except (SoCoException, IndexError, ValueError, SyntaxError, TypeError):
                item = None
        if item is None:
            if routing.music_source(ref.uri) != "LIBRARY":
                raise NotPlayable("favorite metadata is broken")
            # Library content needs no service metadata; a bare item works.
            item = DidlObject(resources=[resource], title=ref.title, parent_id="", item_id="")
        item.resources = [resource]
        return item

    def play_share_link(self, link: ShareLinkRef, title: str, start: StartAt | None = None) -> None:
        self._play_alone()
        coordinator, uid = self._coordinator()
        self._clear_queue(coordinator)
        plugin = ShareLinkPlugin(coordinator)
        first = self._enqueue(
            coordinator,
            lambda: plugin.add_share_link_to_queue(
                plugin_uri(link), dc_title=_xml_text(title), timeout=SLOW_TIMEOUT
            ),
        )
        self._play_queue(coordinator, uid, first, start)

    def _enqueue(self, coordinator: Any, add: Callable[[], Any]) -> int:
        """Run an enqueue call; tolerate a read timeout if items arrived.

        A music service answers 800 now and then (e.g. while it refreshes
        its token): the queue is cleared and the call repeated once.
        """
        try:
            return self._enqueue_once(coordinator, add)
        except ServiceUnavailable as exc:
            if exc.upnp_code != UPNP_SERVICE_ERROR:
                raise
            log.info("Enqueueing failed with UPnP 800; trying once more")
            time.sleep(RETRY_PAUSE)
            self._clear_queue(coordinator)
            return self._enqueue_once(coordinator, add)

    def _enqueue_once(self, coordinator: Any, add: Callable[[], Any]) -> int:
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

    def _play_queue(
        self, coordinator: Any, uid: str, first_track: int, start: StartAt | None = None
    ) -> None:
        self._call(
            coordinator.avTransport.SetAVTransportURI,
            [
                _INSTANCE,
                ("CurrentURI", f"x-rincon-queue:{uid}#0"),
                ("CurrentURIMetaData", ""),
            ],
        )
        # Shuffle and repeat belong to the queue: reset them once the queue
        # is the active source (on a radio stream Sonos would refuse).
        self._normal_play_mode(coordinator)
        first_track = max(first_track, 1)
        track, seconds = first_track, 0
        if start is not None:
            track, seconds = self._resume_point(coordinator, first_track, start)
        self._call(
            coordinator.avTransport.Seek,
            [_INSTANCE, ("Unit", "TRACK_NR"), ("Target", track)],
        )
        # Seeking within a track only works once the service has it loaded:
        # try before Play, and else once more when it plays.
        seek_later = seconds and not self._seek_time(coordinator, seconds)
        self._play(coordinator)
        if seek_later:
            # The album plays already: nothing here may fail the start.
            try:
                if self._wait_until_playing(coordinator):
                    self._seek_time(coordinator, seconds)
            except SonosError as exc:
                log.info("Resume: could not seek after starting (%s)", exc.code)

    def _resume_point(self, coordinator: Any, first_track: int, start: StartAt) -> tuple[int, int]:
        """Where to resume: the saved track and second, if the queue still has
        the same track there (streaming services change the query part)."""
        track = first_track + start.track - 1
        try:
            uri, _ = self._queue_item_at(coordinator, track)
        except SonosError as exc:
            if exc.connection_problem:
                raise
            return first_track, 0
        if uri is None or _without_query(uri) != _without_query(start.track_uri):
            log.info("Resume: the album changed; starting from the beginning")
            return first_track, 0
        # A few seconds back, so the listener finds the thread again.
        seconds = start.seconds - RESUME_REWIND if start.seconds >= RESUME_MIN_SECONDS else 0
        return track, max(seconds, 0)

    def _seek_time(self, coordinator: Any, seconds: int) -> bool:
        target = f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
        try:
            self._call(
                coordinator.avTransport.Seek,
                [_INSTANCE, ("Unit", "REL_TIME"), ("Target", target)],
            )
        except SonosError as exc:
            if exc.connection_problem:
                raise
            log.debug("Seek to %s refused (%s)", target, exc.code)
            return False
        return True

    def _wait_until_playing(self, coordinator: Any) -> bool:
        deadline = time.monotonic() + SEEK_WAIT
        while time.monotonic() < deadline:
            info = self._call(coordinator.avTransport.GetTransportInfo, [_INSTANCE])
            if info.get("CurrentTransportState") == "PLAYING":
                return True
            time.sleep(0.3)
        return False

    def _play(self, coordinator: Any) -> None:
        def play() -> None:
            self._call(coordinator.avTransport.Play, [_INSTANCE, ("Speed", 1)])

        try:
            # Right after a new source is set, Play can fail with 701 while
            # the speaker is still transitioning: try once more.
            self._once_more(play, UPNP_TRANSITION_NOT_AVAILABLE)
        except ActionNotAvailable as exc:
            raise PlaybackFailed("the speaker refused to start playback") from exc

    @staticmethod
    def _once_more(step: Callable[[], Any], upnp_code: int) -> Any:
        """Run ``step``; repeat it once after a short pause on ``upnp_code``."""
        try:
            return step()
        except SonosError as exc:
            if exc.upnp_code != upnp_code:
                raise
            log.info("UPnP error %s; trying once more", upnp_code)
            time.sleep(RETRY_PAUSE)
            return step()

    def _normal_play_mode(self, coordinator: Any) -> None:
        """Switch shuffle and repeat off, so that audio plays keep their order."""
        try:
            self._call(coordinator.avTransport.SetPlayMode, [_INSTANCE, ("NewPlayMode", "NORMAL")])
        except SonosError as exc:
            if exc.connection_problem:
                raise
            log.debug("Could not reset play mode: %s", exc)

    def transport(self, action: str) -> None:
        if action not in TRANSPORT_ACTIONS:
            raise ValueError(f"unknown transport action {action!r}")
        # In a group, "pause" means: the kids room leaves it and is silent,
        # while the other rooms play on.
        self._play_alone()
        coordinator, _ = self._coordinator()
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
        coordinator, _ = self._coordinator()
        info = self._call(coordinator.avTransport.GetTransportInfo, [_INSTANCE])
        media = self._call(coordinator.avTransport.GetMediaInfo, [_INSTANCE])
        media_uri = media.get("CurrentURI") or ""
        first_queue_uri = queue_length = None
        if media_uri.startswith("x-rincon-queue:"):
            first_queue_uri, queue_length = self._queue_item_at(coordinator, 1)
        return Playback(
            state=_STATES.get(info.get("CurrentTransportState", ""), "unknown"),
            media_uri=media_uri,
            first_queue_uri=first_queue_uri,
            actions=frozenset(self._actions(coordinator)),
            queue_length=queue_length,
        )

    def position(self) -> Position | None:
        coordinator, _ = self._coordinator()
        info = self._call(coordinator.avTransport.GetPositionInfo, [_INSTANCE])
        track = _int_or_none(info.get("Track"))
        seconds = _seconds(info.get("RelTime"))
        if not track or seconds is None:
            return None  # e.g. "NOT_IMPLEMENTED" for radio
        return Position(
            track=track,
            seconds=seconds,
            duration=_seconds(info.get("TrackDuration")),
            track_uri=info.get("TrackURI") or "",
            title=_track_title(info.get("TrackMetaData")),
        )

    def _actions(self, coordinator: Any) -> set[str]:
        result = self._call(coordinator.avTransport.GetCurrentTransportActions, [_INSTANCE])
        return {action.split("_")[-1] for action in result.get("Actions", "").split(", ") if action}

    def _queue_item_at(self, coordinator: Any, track: int) -> tuple[str | None, int | None]:
        """The URI of queue item ``track`` (1-based) and the queue's length."""
        result = self._call(
            coordinator.contentDirectory.Browse,
            [
                ("ObjectID", "Q:0"),
                ("BrowseFlag", "BrowseDirectChildren"),
                ("Filter", "*"),
                ("StartingIndex", max(track - 1, 0)),
                ("RequestedCount", 1),
                ("SortCriteria", ""),
            ],
        )
        length = _int_or_none(result.get("TotalMatches"))
        try:
            items = from_didl_string(result.get("Result", ""))
        except (SoCoException, ValueError, SyntaxError):
            return None, length
        if items and items[0].resources:
            return items[0].resources[0].uri, length
        return None, length

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

    def set_mute(self, muted: bool) -> None:
        player = self._room_player()
        self._call(
            player.renderingControl.SetMute,
            [_INSTANCE, ("Channel", "Master"), ("DesiredMute", 1 if muted else 0)],
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
        """Download album art named in favorite metadata.

        Art served by a speaker of this household is fetched from it directly
        (no redirects). Anything else goes through the restricted fetcher:
        public addresses only, every redirect checked, size and time limits.
        """
        if uri.startswith("/"):
            uri = f"http://{self._room_player().ip_address}:1400{uri}"
        try:
            parts = urlsplit(uri)
            host, port = parts.hostname or "", parts.port
        except ValueError as exc:
            raise NotPlayable("unsupported album art URI") from exc
        if parts.scheme == "http" and port == 1400 and host in self._speaker_ips():
            return self._fetch_speaker_art(uri)
        if parts.scheme not in ("http", "https"):
            raise NotPlayable("unsupported album art URI")
        fetcher = self._fetcher_factory()
        try:
            result = fetcher.get(
                uri,
                max_bytes=MAX_ART_BYTES,
                accept="image/*",
                allow=(ANY_HOST,),
                allow_http=True,
            )
        except FetchError as exc:
            raise CommandRejected(f"album art not available: {exc.code}") from exc
        finally:
            fetcher.close()
        if result.status != 200:
            raise CommandRejected(f"album art answered {result.status}")
        return result.body

    def _speaker_ips(self) -> frozenset[str]:
        ips = set(self._known_ips)
        if self._player is not None:
            ips.add(self._player.ip_address)
        return frozenset(ips)

    def _fetch_speaker_art(self, uri: str) -> bytes:
        deadline = self._clock() + ART_DEADLINE
        try:
            response = self._http_get(
                uri, timeout=(REQUEST_TIMEOUT, 5), stream=True, allow_redirects=False
            )
            try:
                if response.status_code != 200:
                    raise CommandRejected(f"album art answered {response.status_code}")
                data = bytearray()
                for chunk in _body_chunks(response):
                    data += chunk
                    if len(data) > MAX_ART_BYTES:
                        raise CommandRejected("album art too large")
                    if self._clock() > deadline:
                        raise SonosTimeout("album art download too slow")
                return bytes(data)
            finally:
                response.close()
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
