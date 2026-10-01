# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runtime: connects the HTTP layer, the tile library and the speaker.

HTTP handlers call the public methods of :class:`Runtime`. Reading state
never touches the network. Commands are handed to worker lanes:

* the *transport lane* resolves the room, starts tiles, sends transport
  commands and polls the playback state every two seconds;
* the *volume lane* runs the volume guard every second and handles
  louder/quieter, so that a slow playback start never delays a correction;
* the *setup lane* searches rooms and tests a newly chosen room, so that the
  parents' page never blocks the kids.

Everything that belongs to the chosen room lives in a :class:`RoomSession`.
Choosing another room swaps the session; results of jobs that still ran for
the old session are dropped, so nothing leaks from one room to the other.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from muckebox import __version__
from muckebox.library import Library, Tile, TileNotFound
from muckebox.localtime import Zone, ZoneResolver, local_time
from muckebox.settings import (
    SettingsError,
    SettingsStore,
    StoredSettings,
    validate_room,
    validate_seed_ip,
)
from muckebox.sonos.backend import TRANSPORT_ACTIONS, SonosBackend
from muckebox.sonos.errors import ActionNotAvailable, RoomNotFound, SonosError
from muckebox.sonos.model import Favorite, Playback, RoomChoice, RoomInfo, Route
from muckebox.storage import atomic_write

from .breaker import CircuitBreaker
from .clock import Clock, SystemClock
from .cooldown import SKIP, SKIP_SECONDS, TILE, TOGGLE, TOGGLE_SECONDS, Cooldown
from .games import MUTE_LEASE, ActiveGame, Games
from .lanes import Lane
from .resume import ResumeStore
from .state import StateCache
from .timekeeper import Refused, TimeKeeper
from .timers import TimersFile
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
POSITION_INTERVAL = 10.0  # seconds between position reads while an album plays
SETUP_WAIT = 20.0  # a room search (discovery plus network scan) or a room test
ROOM_SEARCH_TTL = 15.0  # seconds a room search result is reused
ROOM_SEARCH_MIN_INTERVAL = 5.0  # a new search at most this often
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


def _iso(epoch: float | None, zone: Zone) -> str | None:
    return None if epoch is None else local_time(epoch, zone).isoformat(timespec="seconds")


def _strip_query(uri: str) -> str:
    return uri.split("?", 1)[0]


BackendFactory = Callable[[str, "str | None", "str | None"], SonosBackend]
RoomFinder = Callable[["str | None"], list[RoomChoice]]


class RoomSession:
    """Everything that belongs to the chosen room."""

    def __init__(
        self,
        backend: SonosBackend,
        max_volume: Callable[[], int],
        clock: Clock,
        soft_limit: Callable[[int], int | None] | None = None,
    ) -> None:
        self.backend = backend
        self.guard = VolumeGuard(backend, max_volume, clock, soft_limit)
        self.transport_breaker = CircuitBreaker(clock, min_cooldown=2, max_cooldown=30)
        # The guard must never pause for long, so its breaker caps at 5 s.
        self.volume_breaker = CircuitBreaker(clock, min_cooldown=1, max_cooldown=5)
        self.resolved_at: float | None = None
        self.fixed_volume = False
        self.favorites: tuple[float, list[Favorite]] | None = None


class Runtime:
    def __init__(
        self,
        store: SettingsStore,
        library: Library,
        data_dir: Path,
        *,
        backend_factory: BackendFactory,
        room_finder: RoomFinder,
        clock: Clock | None = None,
        lane_factory: Callable[[str, Callable[[], None] | None, float], Any] = Lane,
        policy: CommandPolicy | None = None,
        zones: ZoneResolver | None = None,
    ) -> None:
        self.store = store
        self.library = library
        self.data_dir = data_dir
        self.backend_factory = backend_factory
        self.room_finder = room_finder
        self.clock = clock or SystemClock()
        self.policy = policy or CommandPolicy()
        self.zones = zones or ZoneResolver()
        self.timers = TimersFile(data_dir / "timers.json")
        self.resume = ResumeStore(data_dir / "resume.json", self.clock)
        self._position_read_at: float | None = None
        self._last_play_state: str | None = None
        self._pruned_rev: int | None = None
        self.keeper = TimeKeeper(store, self.timers, self.clock, self.zones)
        self.games = Games(store, self.timers, self.keeper, self.clock)
        self.cooldown = Cooldown(self.clock)
        self._mute_owner: RoomSession | None = None  # the room a game muted
        self._muted = False
        self._dance_owns_music = False
        self._dance_tile: str | None = None  # a dance tile still starting
        self._pause_sent: float | None = None
        self.transport_lane = lane_factory(
            "transport", self.poll_transport, TRANSPORT_POLL_INTERVAL
        )
        self.volume_lane = lane_factory("volume", self.poll_volume, VOLUME_POLL_INTERVAL)
        self.setup_lane = lane_factory("setup", None, 60.0)
        self.state = StateCache(
            sonos={"status": "not_configured", "room": None},
            playback={"state": "unknown", "tile_id": None},
            actions=[],
            volume=None,
            pending=None,
            last_error=None,
        )
        self._command_lock = threading.Lock()
        self._volume_lock = threading.Lock()
        # Guards the session swap and the shared state. Never held during file
        # or network I/O: the volume guard publishes through it every second.
        self._switch_lock = threading.RLock()
        # Serialises writes of the room to settings.json (choice and renames).
        self._room_lock = threading.Lock()
        # Serialises writes of state.json; see _persist_now_playing().
        self._state_file_lock = threading.Lock()
        self._now_playing_version = 0
        self._saved_version = 0
        self._search_lock = threading.Lock()
        self._searches: dict[str | None, tuple[float, list[RoomChoice]]] = {}
        self._session: RoomSession | None = None
        self._room_uid: str | None = None
        self._now_playing: NowPlaying | None = None
        current = store.current()
        if current.configured:
            self._room_uid = current.room_uid
            self._now_playing = self._load_now_playing(current.room_uid)
            self._install(
                self._new_session(backend_factory(current.room, current.room_uid, current.seed_ip)),
                current.room,
            )

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        # The lanes always run: without a room their idle tasks do nothing,
        # and choosing a room later needs them.
        for lane in (self.transport_lane, self.volume_lane, self.setup_lane):
            lane.start()

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Stop all lanes, waiting at most ``timeout`` seconds in total."""
        lanes = (self.transport_lane, self.volume_lane, self.setup_lane)
        for lane in lanes:
            lane.request_stop()
        deadline = self.clock.monotonic() + timeout
        for lane in lanes:
            lane.join(deadline - self.clock.monotonic())
        self.timers.save()
        self.resume.save(force=True)

    def _new_session(self, backend: SonosBackend) -> RoomSession:
        return RoomSession(backend, self.max_volume, self.clock, self.keeper.soft_limit)

    def volume_limit(self) -> int:
        """The limit right now: the parents' maximum, lower while fading."""
        soft = self.keeper.current_limit()
        return self.max_volume() if soft is None else min(self.max_volume(), soft)

    def max_volume(self) -> int:
        return self.store.current().max_volume

    def volume_step(self) -> int:
        return self.store.current().volume_step

    @property
    def configured(self) -> bool:
        return self._session is not None

    def zone(self) -> Zone:
        return self.zones.zone(self.store.current().time_zone)

    def local_now(self) -> datetime:
        return local_time(self.clock.time(), self.zone())

    # -- reading state (never blocks) ---------------------------------------

    def state_document(self) -> dict[str, Any]:
        data, rev = self.state.snapshot()
        sonos = data["sonos"]
        session = self._session
        if session and sonos.get("status") not in ("ok", "starting", "not_configured"):
            sonos["retry_in"] = session.transport_breaker.retry_in()
        last_error = data["last_error"]
        if last_error and self.clock.time() - last_error["at"] > LAST_ERROR_TTL:
            last_error = None
        playback = data["playback"]
        actions = set(data["actions"])
        playing = playback["state"] in ("playing", "transitioning")
        volume = data["volume"]
        times = self.keeper.document()
        if times["schedule"]["phase"] == "closed":
            actions = set()  # only pause stays possible, and only while playing
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
                "limit": self.volume_limit(),
                "step": self.volume_step(),
            },
            "pending": data["pending"],
            "cooldown": {
                "tile": self.store.current().controls.tap_cooldown,
                "skip": SKIP_SECONDS,
                "toggle": TOGGLE_SECONDS,
            },
            "last_error": last_error,
            **times,
            "games": self.games.document(),
        }

    # -- commands from the kids view ----------------------------------------

    def play_tile(self, tile_id: str, *, tap: bool = False) -> str:
        """Start a tile. Returns "accepted", "resumed" or "noop".

        ``tap``: a kid tapped it on the tablet; counts as activity and is
        subject to the cooldown (internal starts, e.g. a game's music, are not).
        """
        tile = self.library.get(tile_id)  # raises TileNotFound
        if tap:
            self.keeper.touch()
        session = self._check_available("play_tile")
        playback = self.state.get("playback")
        if playback["tile_id"] == tile.id:
            if playback["state"] in ("playing", "transitioning"):
                return "noop"
            # The paused tile again: just like the play button.
            if tap:
                self.cooldown.check(TOGGLE)
            self._submit_exclusive(lambda: self._resume_job(session, tile.id), pending=None)
            if tap:
                self.cooldown.arm(TOGGLE, TOGGLE_SECONDS)
            return "resumed"
        if tap:
            self.cooldown.check(TILE)
        pending = {"action": "start", "tile_id": tile.id, "since": int(self.clock.time())}
        self._submit_exclusive(lambda: self._start_tile(session, tile), pending=pending)
        if tap:
            self.cooldown.arm(TILE, self.store.current().controls.tap_cooldown)
        return "accepted"

    def transport(self, action: str, *, tap: bool = False) -> dict[str, Any]:
        if action not in (*TRANSPORT_ACTIONS, "toggle"):
            raise ValueError(action)
        playing = self.state.get("playback")["state"] in ("playing", "transitioning")
        if action == "toggle":
            action = "pause" if playing else "play"
        if tap:
            self.keeper.touch()
        session = self._check_available(action)
        if (action == "pause" and not playing) or (action == "play" and playing):
            # Already done: a double tap must not undo the first tap.
            return self.state_document()["playback"]
        group, seconds = (
            (SKIP, SKIP_SECONDS)
            if action in ("next", "previous")
            else (
                TOGGLE,
                TOGGLE_SECONDS,
            )
        )
        if tap:
            self.cooldown.check(group)
        future = self._submit_exclusive(lambda: self._transport_job(session, action), pending=None)
        if tap:
            self.cooldown.arm(group, seconds)
        self._wait(future, COMMAND_WAIT, session)
        return self.state_document()["playback"]

    def change_volume(self, direction: str) -> dict[str, Any]:
        if direction not in ("up", "down"):
            raise ValueError(direction)
        self.keeper.touch()  # only the kids' louder/quieter buttons call this
        session = self._check_available(f"volume_{direction}")
        with self._volume_lock:
            # One tap at a time: taps must not pile up while the speaker is slow.
            if self.volume_lane.busy:
                raise Busy()
            future = self.volume_lane.submit(lambda: self._volume_job(session, direction))
        try:
            self._wait(future, VOLUME_WAIT, session)
        except Unavailable as exc:
            raise Unavailable("volume_unknown", exc.retry_in) from exc
        return self.state_document()["volume"]

    # -- commands from the parents' page --------------------------------------

    def favorites(self, refresh: bool = False) -> list[Favorite]:
        session = self._check_available("favorites")
        cached = session.favorites
        if cached and not refresh and self.clock.monotonic() - cached[0] < FAVORITES_TTL:
            return cached[1]
        future = self.transport_lane.submit(lambda: self._favorites_job(session))
        return self._wait(future, FAVORITES_WAIT, session)

    def cached_favorites(self) -> list[Favorite] | None:
        """The last favorites list of this room, however old (no speaker access)."""
        session = self._session
        return session.favorites[1] if session and session.favorites else None

    def fetch_art(self, uri: str) -> bytes:
        """Download album art (runs in the calling thread; no UPnP involved)."""
        session = self._session
        if session is None:
            raise Unavailable("not_configured")
        return session.backend.fetch_art(uri)

    def status(self) -> dict[str, Any]:
        """Diagnostics for parents."""
        session = self._session
        current = self.store.current()
        problems = []
        if session is None:
            problems.append("not_configured")
        if current.pin_generated:
            problems.append("pin_generated")
        if self.store.load_problem:
            problems.append(self.store.load_problem)
        zone = self.zone()
        return {
            "sonos": self.state.get("sonos"),
            "time": {
                "now": local_time(self.clock.time(), zone).isoformat(timespec="seconds"),
                "zone": zone.name,
                "source": zone.source,
            },
            **self.keeper.document(),
            "idle": {
                "minutes": current.controls.idle_minutes,
                "paused_at": _iso(self.keeper.idle_paused_at, zone),
            },
            "games": {
                "used_today": int(self.games.used_today()),
                "daily_seconds": current.games.daily_minutes * 60,
            },
            "config_problems": problems,
            "library_problem": self.library.load_problem,
            "volume_guard": {
                "max": current.max_volume,
                "corrections": session.guard.corrections if session else 0,
                "fighting": session.guard.fighting if session else False,
                "fixed_volume": session.fixed_volume if session else False,
            },
            "breaker": {
                "transport_retry_in": session.transport_breaker.retry_in() if session else None,
                "volume_retry_in": session.volume_breaker.retry_in() if session else None,
            },
        }

    def settings(self) -> StoredSettings:
        return self.store.current()

    # -- setting up the room --------------------------------------------------

    def search_rooms(self, seed_ip: str | None, refresh: bool = False) -> list[RoomChoice]:
        """Find the household's rooms (via ``seed_ip`` or discovery)."""
        cached = self._searches.get(seed_ip)
        if cached:
            age = self.clock.monotonic() - cached[0]
            # Reuse a fresh result; even "search again" at most every few seconds.
            if age < ROOM_SEARCH_MIN_INTERVAL or (not refresh and age < ROOM_SEARCH_TTL):
                return cached[1]
        if not self._search_lock.acquire(blocking=False):
            raise Busy()
        try:
            future = self.setup_lane.submit(lambda: self.room_finder(seed_ip))
            rooms = self._wait(future, SETUP_WAIT)
        finally:
            self._search_lock.release()
        now = self.clock.monotonic()
        self._searches = {
            seed: found
            for seed, found in self._searches.items()
            if now - found[0] < ROOM_SEARCH_TTL
        }
        self._searches[seed_ip] = (now, rooms)
        return rooms

    def choose_room(self, room: object, seed_ip: object) -> RoomInfo:
        """Test the room, then save it and switch to it (nothing is saved on failure).

        Raises :class:`~muckebox.settings.SettingsError` for invalid input,
        :class:`Unavailable` if the room cannot be reached and :class:`Busy`
        while a room search or another test is running.
        """
        room = validate_room(room)
        seed_ip = validate_seed_ip(seed_ip)
        # The speaker ID comes from our own search result, never from the client.
        found = [self._searches.get(seed_ip), *self._searches.values()]
        uid = next((r.uid for f in found if f for r in f[1] if r.name == room), None)
        if not self._search_lock.acquire(blocking=False):
            raise Busy()
        try:
            session = self._new_session(self.backend_factory(room, uid, seed_ip))
            future = self.setup_lane.submit(lambda: self._test_room(session))
            info = self._wait(future, SETUP_WAIT)
        finally:
            self._search_lock.release()
        room_uid = info.player_uid or uid
        self.games.end()  # the watchdog unmutes the room the game muted
        with self._room_lock:
            self.store.set_room(info.name, room_uid, seed_ip)  # raises OSError: nothing changes
            with self._switch_lock:
                if room_uid != self._room_uid:
                    self._set_now_playing(None)  # another room: forget the highlighted tile
                self._room_uid = room_uid
                self._install(session, info.name, grouped=info.grouped, status="ok")
        self._persist_now_playing()
        log.info("Now controlling room %s", info.name)
        return info

    def _test_room(self, session: RoomSession) -> RoomInfo:
        info = session.backend.resolve()
        session.resolved_at = self.clock.monotonic()
        self._detect_fixed_volume(session)
        return info

    def _install(
        self,
        session: RoomSession,
        room: str | None,
        *,
        grouped: bool = False,
        status: str = "starting",
    ) -> None:
        with self._switch_lock:
            self._session = session
            sonos: dict[str, Any] = {"status": status, "room": room}
            if status == "ok":
                sonos["grouped"] = grouped
            self.state.update(
                sonos=sonos,
                playback={"state": "unknown", "tile_id": None},
                actions=[],
                volume=None,
                pending=None,
                last_error=None,
            )

    def _publish(self, session: RoomSession, **sections: Any) -> None:
        """Update the shared state, unless ``session`` has been replaced meanwhile."""
        with self._switch_lock:
            if session is self._session:
                self.state.update(**sections)

    def _current(self, session: RoomSession) -> bool:
        return session is self._session

    # -- lane jobs --------------------------------------------------------

    def poll_transport(self) -> None:
        """Idle task of the transport lane."""
        self.keeper.tidy()
        self._prune_positions()
        self.timers.save()  # here, never in a web request or under a lock
        self.resume.save()
        session = self._session
        if session is None or not session.transport_breaker.allow():
            return
        try:
            self._ensure_room(session)
            playback = self._refresh_playback(session)
            ours = playback.state in ("playing", "transitioning") and bool(self._current_tile())
            self._enforce_time(session, playback, ours)
            if not ours:
                self.keeper.idle_quiet()
            self._record_position(session, playback)
            session.transport_breaker.success()
        except SonosError as exc:
            self._on_error(session, exc)

    def _record_position(self, session: RoomSession, playback: Playback) -> None:
        """Remember where an album tile is, for "Weiterhören"."""
        now_playing = self._now_playing
        tile_id = self.state.get("playback")["tile_id"]
        if not tile_id or now_playing is None or now_playing.route != Route.QUEUE.value:
            return
        try:
            tile = self.library.get(tile_id)
        except TileNotFound:
            return
        if not tile.resumes:
            return
        state = playback.state
        changed, self._last_play_state = state != self._last_play_state, state
        if state == "stopped":
            if changed:
                self.resume.stopped(tile_id)  # heard to the end: next time from the start
            return
        if state not in ("playing", "paused"):
            return
        now = self.clock.monotonic()
        recent = (
            self._position_read_at is not None and now - self._position_read_at < POSITION_INTERVAL
        )
        if recent and not changed:
            return
        self._position_read_at = now
        try:
            position = session.backend.position()
        except SonosError as exc:
            if exc.connection_problem:
                raise
            return
        if position is not None:
            self.resume.record(tile_id, position, playback.queue_length)
        if state == "paused" and changed:
            self.resume.urgent()

    def restart_tile(self, tile_id: str) -> None:
        """ "Von vorn": the tile starts from the beginning next time, even if it
        is the one still loaded (a tap would otherwise just continue it)."""
        self.resume.restart(tile_id)
        with self._switch_lock:
            if self._now_playing is not None and self._now_playing.tile_id == tile_id:
                self._set_now_playing(None)
                self.state.update(playback={**self.state.get("playback"), "tile_id": None})

    def _prune_positions(self) -> None:
        if self._pruned_rev != self.library.rev:
            self._pruned_rev = self.library.rev
            self.resume.prune({tile.id for tile in self.library.tiles()})

    def _current_tile(self) -> str | None:
        return self.state.get("playback")["tile_id"]

    def _enforce_time(self, session: RoomSession, playback: Playback, ours: bool) -> None:
        """Pause once at the end of the usage time or the sleep timer, and
        after a long time without a tap (only a tile; ``ours``: one plays)."""
        if not self._current(session):
            return
        end, mark_done, reason = self.keeper.due_pause(), self.keeper.mark_done, "Usage time over"
        playing = playback.state in ("playing", "transitioning")
        if end is None and (ours or not playing):
            end, mark_done = self.keeper.due_idle_pause(), self.keeper.mark_idle_done
            reason = "No tap on the tablet for a long time"
        if end is None:
            released = self.keeper.fade_released()
            if released is not None:
                self._restore_volume(session, released)
            return
        if playback.state in ("playing", "transitioning"):
            # In a group the kids room leaves it (and is silent); the other
            # rooms play on.
            if self._pause_sent != end:
                log.info("%s: pausing", reason)
                self._pause_sent = end
            try:
                session.backend.transport("pause")
            except ActionNotAvailable:
                log.debug("Pause not available yet; trying again")
            return  # confirmed by the next poll
        if playback.state == "unknown":
            return
        volume = mark_done(end)
        if volume is not None:
            self._restore_volume(session, volume)

    def _restore_volume(self, session: RoomSession, volume: int) -> None:
        """After the pause: back to the volume before the fade, for the next morning."""
        target = min(volume, self.max_volume())

        def job() -> None:
            try:
                session.backend.set_volume(target)
            except SonosError as exc:
                log.info("Could not restore the volume: %s", exc)
                return
            self._publish(session, volume=target)

        self.volume_lane.submit(job)

    def poll_volume(self) -> None:
        """Idle task of the volume lane: the volume guard."""
        session = self._session
        if session is None or not session.volume_breaker.allow():
            return
        try:
            volume = session.guard.step()
        except SonosError as exc:
            if exc.connection_problem:
                session.volume_breaker.failure()
                self._publish(session, volume=None)  # the bar shows "unknown"
            else:
                log.info("Volume check failed: %s", exc)
            return
        session.volume_breaker.success()
        self._publish(session, volume=volume)
        self._game_mute_watchdog(session)

    # -- games --------------------------------------------------------------

    def start_game(self, game_id: str) -> ActiveGame:
        """Start a game: music for the freeze dance, silence for the others."""
        game = self.games.start(game_id)
        self.keeper.touch()
        try:
            if game.id == "freeze_dance":
                self._start_dance_music()
            elif game.id in ("sound_quiz", "move_like"):
                self._pause_for_game()
        except BaseException:
            self.games.end()
            raise
        return game

    def end_game(self) -> None:
        game = self.games.end()
        if game is None or game.id != "freeze_dance":
            return
        session = self._session
        if session is None:
            return
        # The watchdog unmutes the room the game muted; the pause runs on the
        # transport lane after a start that may still be under way.
        self.volume_lane.submit(lambda: self._game_mute_watchdog(session))
        if self._dance_owns_music:
            self.transport_lane.submit(lambda: self._pause_after_dance(session))

    def game_mute(self, muted: bool) -> None:
        """The freeze dance: stop (mute) or dance on (unmute)."""
        game = self.games.active()
        if game is None or game.id != "freeze_dance":
            raise Refused("game_unavailable")
        session = self._session
        if session is None:
            raise Unavailable("not_configured")
        self.games.mute_until = self.clock.monotonic() + MUTE_LEASE if muted else None
        self.volume_lane.submit(lambda: self._apply_mute(session, muted))

    def _start_dance_music(self) -> None:
        """The dance tile, or what plays anyway. Muckebox pauses at the end only
        music it started itself (never the living room's music in a group)."""
        tile_id = self.store.current().games.dance_tile
        shown = self.state.get("playback")
        grouped = self.state.get("sonos").get("grouped")
        self._dance_owns_music = False
        if tile_id:
            self._dance_tile = tile_id  # set first: the start may fail at once
            try:
                result = self.play_tile(tile_id)
            except TileNotFound:
                result = None  # removed meanwhile: dance to whatever there is
            if result != "accepted":
                self._dance_tile = None
            if result is not None:
                self._dance_owns_music = True
                if self.games.active() is None:  # the start failed right away
                    raise Unavailable(self._start_error(tile_id))
                return
        if shown["state"] in ("playing", "transitioning"):
            self._dance_owns_music = not grouped or bool(shown["tile_id"])
            return
        if shown["tile_id"]:
            self.transport("play")
            self._dance_owns_music = True
            return
        raise Refused("dance_music_missing")

    def _start_error(self, tile_id: str) -> str:
        error = self.state.get("last_error")
        return error["code"] if error and error.get("tile_id") == tile_id else "sonos_error"

    def _dance_start_failed(self, tile_id: str) -> None:
        """The dance music did not start: end the game (the time comes back);
        the tablet stops when its next mute is refused."""
        if tile_id == self._dance_tile and self.games.end() is not None:
            log.info("Freeze dance ended: its music did not start")
        self._dance_tile = None

    def _pause_after_dance(self, session: RoomSession) -> None:
        try:
            if session.backend.playback().state in ("playing", "transitioning"):
                session.backend.transport("pause")
        except SonosError as exc:
            log.info("Could not pause after the freeze dance: %s", exc)

    def _pause_for_game(self) -> None:
        """The quiz and "move like" sound from the tablet: pause the speaker."""
        shown = self.state.get("playback")
        if shown["state"] not in ("playing", "transitioning"):
            return
        try:  # in a group the kids room leaves it; the other rooms play on
            self.transport("pause")
        except (Unavailable, Busy, Refused) as exc:
            log.info("Could not pause for the game: %s", exc)

    def _apply_mute(self, session: RoomSession, muted: bool) -> bool:
        if muted:
            with self.timers.read() as state:
                flagged = state.game_mute
            if not flagged:
                # Written before muting: if Muckebox dies now, the next start
                # still unmutes. It stays set until the game is over.
                with self.timers.change() as state:
                    state.game_mute = True
                self.timers.save()
            self._mute_owner = session
        try:
            session.backend.set_mute(muted)
        except SonosError as exc:
            log.info("Could not %s the speaker: %s", "mute" if muted else "unmute", exc)
            return False
        self._muted = muted
        return True

    def _game_mute_watchdog(self, session: RoomSession) -> None:
        """Unmute when the freeze dance stops renewing its mute (tablet gone,
        game over, bedtime, another room): the speaker must never stay silent
        by accident. It unmutes the room the game muted, until that works."""
        with self.timers.read() as state:
            flagged = state.game_mute
        if not flagged:
            return
        target = self._mute_owner or session
        game = self.games.active()
        dancing = (
            game is not None
            and game.id == "freeze_dance"
            and target is session
            and self.keeper.phase().allowed
        )
        if dancing:
            lease = self.games.mute_until
            if self._muted and (lease is None or self.clock.monotonic() >= lease):
                self.games.mute_until = None
                self._apply_mute(target, False)
            return
        if self._apply_mute(target, False):
            self.games.mute_until = None
            self._mute_owner = None
            with self.timers.change() as state:
                state.game_mute = False

    def _ensure_room(self, session: RoomSession, force: bool = False) -> None:
        now = self.clock.monotonic()
        if (
            not force
            and session.resolved_at is not None
            and now - session.resolved_at < RESOLVE_INTERVAL
        ):
            return
        first = session.resolved_at is None
        room = session.backend.resolve()
        session.resolved_at = now
        self._publish(session, sonos={"status": "ok", "room": room.name, "grouped": room.grouped})
        self._remember_rename(session, room)
        if first:
            log.info("Controlling room %s", room.name)
            self._detect_fixed_volume(session)
            self._unmute_after_crash(session)

    def _unmute_after_crash(self, session: RoomSession) -> None:
        """A game muted the speaker and Muckebox stopped before it ended: unmute."""
        with self.timers.read() as state:
            muted = state.game_mute
        if not muted:
            return
        try:
            session.backend.set_mute(False)
        except SonosError as exc:
            if exc.connection_problem:
                raise
            log.warning("Could not unmute the speaker: %s", exc)
            return
        log.info("Unmuted the speaker (a game had muted it)")
        with self.timers.change() as state:
            state.game_mute = False

    def _remember_rename(self, session: RoomSession, room: RoomInfo) -> None:
        """Keep the stored room name up to date after a rename in the Sonos app."""
        current = self.store.current()
        room_uid = room.player_uid or current.room_uid
        if (room.name, room_uid) == (current.room, current.room_uid):
            return
        # Under the room lock, so that a room chosen meanwhile is never
        # overwritten with the old room's name.
        with self._room_lock:
            if not self._current(session):
                return
            try:
                self.store.set_room(room.name, room_uid, current.seed_ip)
            except (SettingsError, OSError) as exc:
                log.warning("Could not save the new room name: %s", exc)
                return
            with self._switch_lock:
                self._room_uid = room_uid

    def _detect_fixed_volume(self, session: RoomSession) -> None:
        try:
            session.fixed_volume = session.backend.fixed_volume()
        except SonosError:
            session.fixed_volume = False
        if session.fixed_volume:
            log.warning("The room's volume is fixed; the volume limit has no effect")

    def _refresh_playback(self, session: RoomSession) -> Playback:
        playback = session.backend.playback()
        with self._switch_lock:
            if self._current(session):
                self.state.update(
                    playback={"state": playback.state, "tile_id": self._match(playback)},
                    actions=sorted(playback.actions),
                )
        self._persist_now_playing()
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

    def _start_tile(self, session: RoomSession, tile: Tile) -> None:
        try:
            start = self.resume.get(tile.id) if tile.resumes else None
            self.resume.started(tile.id)
            self.resume.urgent()  # the tile that played before is saved soon
            self._last_play_state = None
            try:
                self._ensure_room(session)
                if tile.kind == "favorite":
                    route = session.backend.play_favorite(
                        tile.favorite_ref(), tile.favorite_route(), start
                    )
                else:
                    session.backend.play_share_link(tile.share_link(), tile.title, start)
                    route = Route.QUEUE
            except SonosError as exc:
                log.warning("Starting tile %s failed: %s", tile.id, exc)
                self._on_error(session, exc)
                self._report_error(session, exc.code, tile.id)
                self._dance_start_failed(tile.id)
                return
            except Exception:
                log.exception("Starting tile %s failed unexpectedly", tile.id)
                self._report_error(session, "sonos_error", tile.id)
                self._dance_start_failed(tile.id)
                return
            # The speaker accepted the start: remember it, whatever happens next.
            with self._switch_lock:
                if self._current(session):
                    self._set_now_playing(
                        NowPlaying(
                            tile_id=tile.id, route=route.value, uri=tile.source.get("uri", "")
                        )
                    )
                    self.state.update(last_error=None)
            self._persist_now_playing()
            self._left_group(session)
            session.transport_breaker.success()
            try:
                self._refresh_playback(session)  # also learns the first queue item
            except SonosError as exc:
                log.info("Reading the state after starting %s failed: %s", tile.id, exc)
                if exc.connection_problem:
                    self._on_error(session, exc)
        finally:
            # Always clear "pending", even for a replaced session: the kids
            # view must never stay locked.
            self.state.update(pending=None)

    def _resume_job(self, session: RoomSession, tile_id: str) -> None:
        try:
            self._transport_job(session, "play")
        except SonosError as exc:
            self._report_error(session, exc.code, tile_id)
        except Exception:
            log.exception("Resuming tile %s failed unexpectedly", tile_id)
            self._report_error(session, "sonos_error", tile_id)

    def _report_error(self, session: RoomSession, code: str, tile_id: str) -> None:
        self._publish(
            session, last_error={"code": code, "tile_id": tile_id, "at": int(self.clock.time())}
        )

    def _transport_job(self, session: RoomSession, action: str) -> None:
        try:
            session.backend.transport(action)
        except ActionNotAvailable:
            log.debug("Transport action %s not available right now", action)
        except SonosError as exc:
            self._on_error(session, exc)
            raise
        self._left_group(session)
        self._refresh_playback(session)

    def _left_group(self, session: RoomSession) -> None:
        """The kids room may just have left its group: show it at once."""
        if not self.state.get("sonos").get("grouped"):
            return
        try:
            self._ensure_room(session, force=True)
        except SonosError as exc:
            log.info("Could not look up the room again: %s", exc)

    def _volume_job(self, session: RoomSession, direction: str) -> int:
        try:
            volume = session.guard.change(direction, self.volume_step())
        except SonosError as exc:
            if exc.connection_problem:
                session.volume_breaker.failure()
            raise
        session.volume_breaker.success()
        self._publish(session, volume=volume)
        return volume

    def _favorites_job(self, session: RoomSession) -> list[Favorite]:
        try:
            self._ensure_room(session)
            favorites = session.backend.list_favorites()
        except SonosError as exc:
            self._on_error(session, exc)
            raise
        session.favorites = (self.clock.monotonic(), favorites)
        return favorites

    # -- helpers ----------------------------------------------------------

    def _check_available(self, command: str) -> RoomSession:
        session = self._session
        if session is None:
            raise Unavailable("not_configured")
        if session.transport_breaker.is_open:
            status = self.state.get("sonos").get("status")
            raise Unavailable(status or "sonos_unreachable", session.transport_breaker.retry_in())
        self.keeper.check(command)
        self.policy.check(command)
        return session

    def _submit_exclusive(self, job: Callable[[], Any], pending: dict | None) -> Future:
        """Submit a transport job unless another one is running or queued."""
        with self._command_lock:
            if self.transport_lane.busy or self.state.get("pending"):
                raise Busy()
            if pending:
                self.state.update(pending=pending)
            return self.transport_lane.submit(job)

    def _wait(self, future: Future, timeout: float, session: RoomSession | None = None) -> Any:
        try:
            return future.result(timeout)
        except FutureTimeout as exc:
            future.cancel()  # a command reported as failed must not run later
            raise Unavailable("sonos_timeout") from exc
        except SonosError as exc:
            retry = session.transport_breaker.retry_in() if session else None
            raise Unavailable(exc.code, retry) from exc
        except Exception as exc:
            log.exception("A command failed unexpectedly")
            raise Unavailable("sonos_error") from exc

    def _on_error(self, session: RoomSession, exc: SonosError) -> None:
        if exc.connection_problem:
            session.transport_breaker.failure()
            session.resolved_at = None
            self._publish(
                session,
                sonos={"status": exc.code, "room": self.state.get("sonos").get("room")},
                playback={"state": "unknown", "tile_id": None},
                actions=[],
            )
            if self._current(session):
                detail = f": {exc}" if isinstance(exc, RoomNotFound) else ""
                log.warning(
                    "Sonos not reachable (%s%s); retrying in %ss",
                    exc.code,
                    detail,
                    session.transport_breaker.retry_in(),
                )
        else:
            log.info("Sonos command failed: %s (%s)", exc.code, exc)

    def _state_file(self) -> Path:
        return self.data_dir / "state.json"

    def _set_now_playing(self, now_playing: NowPlaying | None) -> None:
        """Remember what Muckebox started (call with _switch_lock held).

        Only memory changes here; _persist_now_playing() writes the file
        later, outside the lock, because a sleeping NAS disk can take seconds.
        """
        if now_playing == self._now_playing:
            return
        self._now_playing = now_playing
        self._now_playing_version += 1

    def _persist_now_playing(self) -> None:
        """Write the latest highlight to state.json (never call with _switch_lock held)."""
        with self._state_file_lock:
            with self._switch_lock:
                version = self._now_playing_version
                now_playing, room_uid = self._now_playing, self._room_uid
            if version == self._saved_version:
                return
            try:
                path = self._state_file()
                if now_playing is None:
                    path.unlink(missing_ok=True)
                else:
                    data = {"room_uid": room_uid, "now_playing": asdict(now_playing)}
                    atomic_write(path, json.dumps(data).encode("utf-8"))
            except OSError as exc:
                log.warning("Could not save state.json: %s", exc)
            self._saved_version = version  # also after a failure: no retry storm

    def _load_now_playing(self, room_uid: str | None) -> NowPlaying | None:
        try:
            data = json.loads(self._state_file().read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("room_uid") != room_uid:
                return None  # started in another room (or not our file)
            return NowPlaying(**data["now_playing"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
