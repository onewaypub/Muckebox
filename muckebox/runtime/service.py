# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runtime: connects the HTTP layer, the tile library and the speaker.

HTTP handlers call the public methods of :class:`Runtime`. Reading state
never touches the network. Commands are handed to one of two worker lanes:

* the *transport lane* resolves the room, starts tiles, sends transport
  commands and polls the playback state every two seconds;
* the *volume lane* runs the volume guard every second and handles
  louder/quieter, so that a slow playback start never delays a correction.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from muckebox import __version__
from muckebox.config import Settings
from muckebox.library import Library, Tile
from muckebox.sonos.backend import TRANSPORT_ACTIONS, SonosBackend
from muckebox.sonos.errors import ActionNotAvailable, RoomNotFound, SonosError
from muckebox.sonos.model import Favorite, Playback, Route

from .breaker import CircuitBreaker
from .clock import Clock, SystemClock
from .lanes import Lane
from .state import StateCache
from .volume_guard import VolumeGuard

log = logging.getLogger(__name__)

API_VERSION = 1
TRANSPORT_POLL_INTERVAL = 2.0
VOLUME_POLL_INTERVAL = 1.0
RESOLVE_INTERVAL = 60.0
LAST_ERROR_TTL = 60.0
COMMAND_WAIT = 3.5
VOLUME_WAIT = 2.5
FAVORITES_WAIT = 15.0
FAVORITES_TTL = 60.0
# Seconds to wait for running Sonos calls when shutting down (after the web
# server has let running requests finish, which takes up to 5 s itself).
STOP_TIMEOUT = 2.0


class Unavailable(Exception):
    """The speaker cannot be controlled right now."""

    def __init__(self, code: str, retry_in: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_in = retry_in


class Busy(Exception):
    """Another command is still running."""


@dataclass
class NowPlaying:
    """What Muckebox itself started last, to highlight the right tile."""

    tile_id: str
    route: str  # "direct" or "queue"
    uri: str = ""  # media URI for direct tiles
    first_queue_uri: str | None = None  # first queue item for queue tiles


class CommandPolicy:
    """Decides whether a command is allowed right now.

    Allows everything in v1; later features (sleep timer, bedtime lock)
    replace it without touching the API or the lanes.
    """

    def check(self, command: str) -> None:
        """Raise :class:`Unavailable` to refuse ``command``."""


def _strip_query(uri: str) -> str:
    return uri.split("?", 1)[0]


class Runtime:
    def __init__(
        self,
        settings: Settings,
        backend: SonosBackend,
        library: Library,
        *,
        clock: Clock | None = None,
        lane_factory: Callable[[str, Callable[[], None] | None, float], Any] = Lane,
        policy: CommandPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.library = library
        self.clock = clock or SystemClock()
        self.policy = policy or CommandPolicy()
        self.transport_lane = lane_factory(
            "transport", self.poll_transport, TRANSPORT_POLL_INTERVAL
        )
        self.volume_lane = lane_factory("volume", self.poll_volume, VOLUME_POLL_INTERVAL)
        self.transport_breaker = CircuitBreaker(self.clock, min_cooldown=2, max_cooldown=30)
        # The guard must never pause for long, so its breaker caps at 5 s.
        self.volume_breaker = CircuitBreaker(self.clock, min_cooldown=1, max_cooldown=5)
        self.guard = VolumeGuard(backend, self.max_volume, self.clock)
        self.state = StateCache(
            sonos={"status": "starting", "room": None},
            playback={"state": "unknown", "tile_id": None},
            actions=[],
            volume=None,
            pending=None,
            last_error=None,
        )
        self._command_lock = threading.Lock()
        self._volume_lock = threading.Lock()
        self._room_resolved_at: float | None = None
        self._now_playing: NowPlaying | None = self._load_now_playing()
        self._favorites: tuple[float, list[Favorite]] | None = None
        self.fixed_volume = False
        if not settings.sonos_config_ok:
            code = next(p.code for p in settings.problems if p.severity == "error")
            self.state.update(sonos={"status": "config_error", "room": None, "problem": code})

    # -- lifecycle --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.settings.sonos_config_ok

    def start(self) -> None:
        if not self.enabled:
            log.warning("Sonos control disabled because of configuration errors")
            return
        self.transport_lane.start()
        self.volume_lane.start()

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Stop both lanes, waiting at most ``timeout`` seconds in total."""
        for lane in (self.transport_lane, self.volume_lane):
            lane.request_stop()
        deadline = self.clock.monotonic() + timeout
        for lane in (self.transport_lane, self.volume_lane):
            lane.join(deadline - self.clock.monotonic())

    def max_volume(self) -> int:
        return self.settings.max_volume

    # -- reading state (never blocks) ---------------------------------------

    def state_document(self) -> dict[str, Any]:
        data, rev = self.state.snapshot()
        sonos = data["sonos"]
        if sonos.get("status") not in ("ok", "starting", "config_error"):
            sonos["retry_in"] = self.transport_breaker.retry_in()
        last_error = data["last_error"]
        if last_error and self.clock.time() - last_error["at"] > LAST_ERROR_TTL:
            last_error = None
        playback = data["playback"]
        actions = set(data["actions"])
        playing = playback["state"] in ("playing", "transitioning")
        volume = data["volume"]
        return {
            "ok": True,
            "api": API_VERSION,
            "version": __version__,
            "state_rev": rev,
            "library_rev": self.library.rev,
            "sonos": sonos,
            "playback": {
                "state": playback["state"],
                "tile_id": playback["tile_id"],
                "can_toggle": playing or "Play" in actions,
                "can_next": "Next" in actions,
                "can_prev": "Previous" in actions,
            },
            "volume": {
                "value": volume,
                "max": self.max_volume(),
                "step": self.settings.volume_step,
            },
            "pending": data["pending"],
            "last_error": last_error,
        }

    # -- commands from the kids view ----------------------------------------

    def play_tile(self, tile_id: str) -> str:
        """Start a tile. Returns "accepted", "resumed" or "noop"."""
        tile = self.library.get(tile_id)  # raises TileNotFound
        self._check_available("play")
        playback = self.state.get("playback")
        if playback["tile_id"] == tile.id:
            if playback["state"] in ("playing", "transitioning"):
                return "noop"
            self._submit_exclusive(lambda: self._resume_job(tile.id), pending=None)
            return "resumed"
        pending = {"action": "start", "tile_id": tile.id, "since": int(self.clock.time())}
        self._submit_exclusive(lambda: self._start_tile(tile), pending=pending)
        return "accepted"

    def transport(self, action: str) -> dict[str, Any]:
        if action not in (*TRANSPORT_ACTIONS, "toggle"):
            raise ValueError(action)
        self._check_available(action)
        playing = self.state.get("playback")["state"] in ("playing", "transitioning")
        if action == "toggle":
            action = "pause" if playing else "play"
        elif (action == "pause" and not playing) or (action == "play" and playing):
            # Already done: a double tap must not undo the first tap.
            return self.state_document()["playback"]
        future = self._submit_exclusive(lambda: self._transport_job(action), pending=None)
        self._wait(future, COMMAND_WAIT)
        return self.state_document()["playback"]

    def change_volume(self, direction: str) -> dict[str, Any]:
        if direction not in ("up", "down"):
            raise ValueError(direction)
        self._check_available("volume")
        with self._volume_lock:
            # One tap at a time: taps must not pile up while the speaker is slow.
            if self.volume_lane.busy:
                raise Busy()
            future = self.volume_lane.submit(lambda: self._volume_job(direction))
        try:
            self._wait(future, VOLUME_WAIT)
        except Unavailable as exc:
            raise Unavailable("volume_unknown", exc.retry_in) from exc
        return self.state_document()["volume"]

    # -- commands from the parents' page --------------------------------------

    def favorites(self, refresh: bool = False) -> list[Favorite]:
        self._check_available("favorites")
        cached = self._favorites
        if cached and not refresh and self.clock.monotonic() - cached[0] < FAVORITES_TTL:
            return cached[1]
        future = self.transport_lane.submit(self._favorites_job)
        return self._wait(future, FAVORITES_WAIT)

    def cached_favorites(self) -> list[Favorite] | None:
        """The last favorites list, however old (no speaker access)."""
        return self._favorites[1] if self._favorites else None

    def fetch_art(self, uri: str) -> bytes:
        """Download album art (runs in the calling thread; no UPnP involved)."""
        return self.backend.fetch_art(uri)

    def status(self) -> dict[str, Any]:
        """Diagnostics for parents."""
        room = self.state.get("sonos")
        return {
            "sonos": room,
            "config_problems": [p.code for p in self.settings.problems],
            "library_problem": self.library.load_problem,
            "volume_guard": {
                "max": self.max_volume(),
                "corrections": self.guard.corrections,
                "fighting": self.guard.fighting,
                "fixed_volume": self.fixed_volume,
            },
            "breaker": {
                "transport_retry_in": self.transport_breaker.retry_in(),
                "volume_retry_in": self.volume_breaker.retry_in(),
            },
        }

    # -- lane jobs --------------------------------------------------------

    def poll_transport(self) -> None:
        """Idle task of the transport lane."""
        if not self.transport_breaker.allow():
            return
        try:
            self._ensure_room()
            self._refresh_playback()
            self.transport_breaker.success()
        except SonosError as exc:
            self._on_error(exc)

    def poll_volume(self) -> None:
        """Idle task of the volume lane: the volume guard."""
        if not self.volume_breaker.allow():
            return
        try:
            volume = self.guard.step()
        except SonosError as exc:
            if exc.connection_problem:
                self.volume_breaker.failure()
                self.state.update(volume=None)  # the bar shows "unknown", buttons disable
            else:
                log.info("Volume check failed: %s", exc)
            return
        self.volume_breaker.success()
        self.state.update(volume=volume)

    def _ensure_room(self, force: bool = False) -> None:
        now = self.clock.monotonic()
        if (
            not force
            and self._room_resolved_at is not None
            and now - self._room_resolved_at < RESOLVE_INTERVAL
        ):
            return
        first = self._room_resolved_at is None
        room = self.backend.resolve()
        self._room_resolved_at = now
        self.state.update(sonos={"status": "ok", "room": room.name, "grouped": room.grouped})
        if first:
            log.info("Controlling room %s", room.name)
            try:
                self.fixed_volume = self.backend.fixed_volume()
            except SonosError:
                self.fixed_volume = False
            if self.fixed_volume:
                log.warning("The room's volume is fixed; the volume limit has no effect")

    def _refresh_playback(self) -> Playback:
        playback = self.backend.playback()
        self.state.update(
            playback={"state": playback.state, "tile_id": self._match(playback)},
            actions=sorted(playback.actions),
        )
        return playback

    def _match(self, playback: Playback) -> str | None:
        now_playing = self._now_playing
        if now_playing is None:
            return None
        if now_playing.route == Route.DIRECT:
            matches = _strip_query(playback.media_uri) == _strip_query(now_playing.uri)
        elif now_playing.first_queue_uri is None and playback.media_uri.startswith(
            "x-rincon-queue:"
        ):
            # Started by us, but the queue could not be read yet: learn it now.
            now_playing = NowPlaying(
                now_playing.tile_id, now_playing.route, now_playing.uri, playback.first_queue_uri
            )
            self._set_now_playing(now_playing)
            matches = playback.first_queue_uri is not None
        else:
            matches = (
                playback.media_uri.startswith("x-rincon-queue:")
                and playback.first_queue_uri is not None
                and playback.first_queue_uri == now_playing.first_queue_uri
            )
        if not matches and playback.state != "transitioning":
            # Somebody played something else: forget our tile.
            self._set_now_playing(None)
            return None
        return now_playing.tile_id if matches else None

    def _start_tile(self, tile: Tile) -> None:
        try:
            try:
                self._ensure_room()
                if tile.kind == "favorite":
                    route = self.backend.play_favorite(tile.favorite_ref(), tile.favorite_route())
                else:
                    self.backend.play_share_link(tile.share_link(), tile.title)
                    route = Route.QUEUE
            except SonosError as exc:
                log.warning("Starting tile %s failed: %s", tile.id, exc)
                self._on_error(exc)
                self._report_error(exc.code, tile.id)
                return
            except Exception:
                log.exception("Starting tile %s failed unexpectedly", tile.id)
                self._report_error("sonos_error", tile.id)
                return
            # The speaker accepted the start: remember it, whatever happens next.
            self._set_now_playing(
                NowPlaying(tile_id=tile.id, route=route.value, uri=tile.source.get("uri", ""))
            )
            self.state.update(last_error=None)
            self.transport_breaker.success()
            try:
                self._refresh_playback()  # also learns the first queue item
            except SonosError as exc:
                log.info("Reading the state after starting %s failed: %s", tile.id, exc)
                if exc.connection_problem:
                    self._on_error(exc)
        finally:
            self.state.update(pending=None)

    def _resume_job(self, tile_id: str) -> None:
        try:
            self._transport_job("play")
        except SonosError as exc:
            self._report_error(exc.code, tile_id)
        except Exception:
            log.exception("Resuming tile %s failed unexpectedly", tile_id)
            self._report_error("sonos_error", tile_id)

    def _report_error(self, code: str, tile_id: str) -> None:
        self.state.update(
            last_error={"code": code, "tile_id": tile_id, "at": int(self.clock.time())}
        )

    def _transport_job(self, action: str) -> None:
        try:
            self.backend.transport(action)
        except ActionNotAvailable:
            log.debug("Transport action %s not available right now", action)
        except SonosError as exc:
            self._on_error(exc)
            raise
        self._refresh_playback()

    def _volume_job(self, direction: str) -> int:
        try:
            volume = self.guard.change(direction, self.settings.volume_step)
        except SonosError as exc:
            if exc.connection_problem:
                self.volume_breaker.failure()
            raise
        self.volume_breaker.success()
        self.state.update(volume=volume)
        return volume

    def _favorites_job(self) -> list[Favorite]:
        try:
            self._ensure_room()
            favorites = self.backend.list_favorites()
        except SonosError as exc:
            self._on_error(exc)
            raise
        self._favorites = (self.clock.monotonic(), favorites)
        return favorites

    # -- helpers ----------------------------------------------------------

    def _check_available(self, command: str) -> None:
        if not self.enabled:
            raise Unavailable("config_error")
        sonos = self.state.get("sonos")
        if self.transport_breaker.is_open:
            raise Unavailable(
                sonos.get("status") or "sonos_unreachable", self.transport_breaker.retry_in()
            )
        self.policy.check(command)

    def _submit_exclusive(self, job: Callable[[], Any], pending: dict | None) -> Future:
        """Submit a transport job unless another one is running or queued."""
        with self._command_lock:
            if self.transport_lane.busy or self.state.get("pending"):
                raise Busy()
            if pending:
                self.state.update(pending=pending)
            return self.transport_lane.submit(job)

    def _wait(self, future: Future, timeout: float) -> Any:
        try:
            return future.result(timeout)
        except FutureTimeout as exc:
            future.cancel()  # a command reported as failed must not run later
            raise Unavailable("sonos_timeout") from exc
        except SonosError as exc:
            raise Unavailable(exc.code, self.transport_breaker.retry_in()) from exc
        except Exception as exc:
            log.exception("A command failed unexpectedly")
            raise Unavailable("sonos_error") from exc

    def _on_error(self, exc: SonosError) -> None:
        if exc.connection_problem:
            self.transport_breaker.failure()
            self._room_resolved_at = None
            self.state.update(
                sonos={"status": exc.code, "room": self.state.get("sonos").get("room")},
                playback={"state": "unknown", "tile_id": None},
                actions=[],
            )
            detail = f": {exc}" if isinstance(exc, RoomNotFound) else ""
            log.warning(
                "Sonos not reachable (%s%s); retrying in %ss",
                exc.code,
                detail,
                self.transport_breaker.retry_in(),
            )
        else:
            log.info("Sonos command failed: %s (%s)", exc.code, exc)

    def _state_file(self) -> Path:
        return self.settings.data_dir / "state.json"

    def _set_now_playing(self, now_playing: NowPlaying | None) -> None:
        if now_playing == self._now_playing:
            return
        self._now_playing = now_playing
        try:
            path = self._state_file()
            if now_playing is None:
                path.unlink(missing_ok=True)
            else:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"now_playing": asdict(now_playing)}), encoding="utf-8")
                tmp.replace(path)
        except OSError as exc:
            log.warning("Could not save state.json: %s", exc)

    def _load_now_playing(self) -> NowPlaying | None:
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
            return NowPlaying(**data["now_playing"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
