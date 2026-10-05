# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The kids' light buttons: Hue scenes of their room, on their own lane.

Nothing here runs on the transport or volume lane, so a slow or missing
bridge never delays the music. The lane polls the bridge every few seconds
(which scene is active: someone may switch from the Hue app or a wall
switch) and notices the end of the sleep timer itself, so that the lights
follow it even while the speaker is unreachable.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import Any

from muckebox.hue.client import HueBackend, HueConnector
from muckebox.hue.errors import HueError, HueNotFound
from muckebox.hue.model import BridgeInfo, Room, Scene
from muckebox.settings import HueBridge, SettingsStore, StoredSettings

from .breaker import CircuitBreaker
from .clock import Clock
from .cooldown import LIGHT, LIGHT_SECONDS, Cooldown
from .lanes import Lane
from .timekeeper import PAUSE_GRACE
from .timers import TimersFile

log = logging.getLogger(__name__)

POLL_INTERVAL = 5.0
ROOMS_INTERVAL = 300.0  # rooms rarely change; their light groups are re-read this often
COMMAND_WAIT = 4.0


class LightsUnavailable(Exception):
    """The lights cannot be switched right now (code for the API)."""

    def __init__(self, code: str, retry_in: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_in = retry_in


class Lights:
    def __init__(
        self,
        store: SettingsStore,
        timers: TimersFile,
        clock: Clock,
        connector: HueConnector,
        lane_factory: Callable[[str, Callable[[], None] | None, float], Any] = Lane,
    ) -> None:
        self.store = store
        self.timers = timers
        self.clock = clock
        self.connector = connector
        self.cooldown = Cooldown(clock)
        self.breaker = CircuitBreaker(clock, min_cooldown=2, max_cooldown=60)
        self.lane = lane_factory("lights", self.poll, POLL_INTERVAL)
        self._lock = threading.Lock()
        self._client: tuple[HueBridge, HueBackend] | None = None
        self._rooms: list[Room] | None = None
        self._rooms_read_at: float | None = None
        self._scenes: list[Scene] | None = None
        self.available = False
        #: Why the bridge cannot be used (an error code), or None.
        self.problem: str | None = None

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        self.lane.start()

    def request_stop(self) -> None:
        self.lane.request_stop()

    def join(self, timeout: float) -> None:
        self.lane.join(timeout)

    # -- what the kids view shows ---------------------------------------------------

    def document(self) -> dict[str, Any]:
        hue = self.store.current().hue
        if hue.bridge is None or hue.room is None or not hue.slots:
            return {"available": False, "slots": [], "cooldown": LIGHT_SECONDS}
        with self._lock:
            scenes = {scene.id: scene for scene in self._scenes} if self._scenes else None
            available = self.available
        slots = []
        for number, slot in enumerate(hue.slots, 1):
            scene = scenes.get(slot.scene) if scenes is not None else None
            if scenes is not None and scene is None:
                continue  # deleted in the Hue app
            slots.append(
                {"slot": number, "picture": slot.picture, "active": bool(scene and scene.active)}
            )
        return {"available": available, "slots": slots, "cooldown": LIGHT_SECONDS}

    # -- the kids' tap ------------------------------------------------------------------

    def toggle(self, slot: int) -> dict[str, Any]:
        """Switch the slot's scene on, or the room off if the scene is on."""
        settings = self.store.current()
        hue = settings.hue
        if hue.bridge is None or hue.room is None:
            raise LightsUnavailable("hue_not_configured")
        if not 1 <= slot <= len(hue.slots):
            raise LookupError(slot)
        self.cooldown.check(LIGHT)
        if not self.breaker.allow():
            raise LightsUnavailable(self.problem or "hue_unreachable", self.breaker.retry_in())
        scene_id = hue.slots[slot - 1].scene
        future = self.lane.submit(lambda: self._switch(settings, scene_id))
        self.cooldown.arm(LIGHT, LIGHT_SECONDS)
        self._wait(future)
        return self.document()

    def _switch(self, settings: StoredSettings, scene_id: str) -> None:
        try:
            client = self._client_for(settings.hue.bridge)
            scenes = client.scenes()
            scene = next((s for s in scenes if s.id == scene_id), None)
            if scene is None:
                raise HueNotFound(scene_id)
            if scene.active:
                group = self._group_of(client, settings.hue.room)
                if group is None:
                    raise HueNotFound(settings.hue.room or "")
                client.off(group)
            else:
                client.recall(scene_id)
            self._read_scenes(client)
        except HueError as exc:
            self._failed(exc)
            raise
        self._succeeded()

    def _wait(self, future: Future) -> None:
        try:
            future.result(COMMAND_WAIT)
        except FutureTimeout as exc:
            future.cancel()
            raise LightsUnavailable("hue_unreachable") from exc
        except HueError as exc:
            raise LightsUnavailable(exc.code, self.breaker.retry_in()) from exc

    # -- the lane --------------------------------------------------------------------------

    def poll(self) -> None:
        """Idle task of the light lane: the active scene and the sleep timer's end."""
        settings = self.store.current()
        bridge = settings.hue.bridge
        if bridge is None:
            self._forget()
            self._sleep_end(settings, None)
            return
        if not self.breaker.allow():
            self._sleep_end(settings, None)
            return
        try:
            client = self._client_for(bridge)
            self._read_scenes(client)
            self._succeeded()
        except HueError as exc:
            self._failed(exc)
            client = None
        self._sleep_end(settings, client)

    def _client_for(self, bridge: HueBridge | None) -> HueBackend:
        if bridge is None:
            raise HueNotFound("bridge")
        with self._lock:
            if self._client is None or self._client[0] != bridge:
                self._client = (
                    bridge,
                    self.connector.client(bridge.ip, bridge.key, bridge.fingerprint),
                )
                self._rooms = None
            return self._client[1]

    def _read_scenes(self, client: HueBackend) -> None:
        scenes = client.scenes()
        with self._lock:
            self._scenes = scenes

    def _group_of(self, client: HueBackend, room_id: str | None) -> str | None:
        now = self.clock.monotonic()
        stale = self._rooms_read_at is None or now - self._rooms_read_at > ROOMS_INTERVAL
        if self._rooms is None or stale:
            self._rooms = client.rooms()
            self._rooms_read_at = now
        room = next((r for r in self._rooms if r.id == room_id), None)
        return room.grouped_light if room else None

    def _succeeded(self) -> None:
        self.breaker.success()
        with self._lock:
            self.available, self.problem = True, None

    def _failed(self, exc: HueError) -> None:
        if isinstance(exc, HueNotFound):
            return  # the bridge works; only that scene or room is gone
        log.info("Hue bridge: %s", exc)
        self.breaker.failure()
        with self._lock:
            self.available, self.problem = False, exc.code

    def _forget(self) -> None:
        with self._lock:
            self._client, self._rooms, self._scenes = None, None, None
            self.available, self.problem = False, None
        self.breaker.success()

    # -- the sleep timer's end -------------------------------------------------------------

    def _sleep_end(self, settings: StoredSettings, client: HueBackend | None) -> None:
        """Switch the lights once when the sleep timer has ended (within 15 minutes)."""
        now = self.clock.time()
        with self.timers.read() as state:
            sleep, done = state.sleep, state.lights_done_end
        if sleep is None or now < sleep.ends_at or done == sleep.ends_at:
            return
        if now - sleep.ends_at > PAUSE_GRACE:
            self._mark_sleep_done(sleep.ends_at)  # too late: never switch hours later
            return
        action = settings.sleep_timer.lights
        if action == "keep" or settings.hue.bridge is None:
            self._mark_sleep_done(sleep.ends_at)
            return
        if client is None:
            return  # the bridge is away: try again on the next poll
        try:
            if action == "off":
                group = self._group_of(client, settings.hue.room)
                if group is not None:
                    client.off(group)
            else:
                client.recall(action)
            self._read_scenes(client)
        except HueNotFound:
            log.info("Sleep timer: the chosen scene or room is gone; lights left as they are")
        except HueError as exc:
            self._failed(exc)
            return
        log.info("Sleep timer over: lights %s", "off" if action == "off" else "to the chosen scene")
        self._mark_sleep_done(sleep.ends_at)

    def _mark_sleep_done(self, end: float) -> None:
        with self.timers.change() as state:
            state.lights_done_end = end

    # -- the parents' page -------------------------------------------------------------------

    def search(self, ip: str | None) -> list[BridgeInfo]:
        return self._run(lambda: self.connector.find(ip), timeout=12.0)

    def pair(self, ip: str) -> HueBridge:
        """Ask the bridge for a key (the parents press its button first)."""
        pairing = self._run(lambda: self.connector.pair(ip))
        info = pairing.info
        bridge = HueBridge(info.ip, info.id, info.name[:60] or "Hue Bridge", pairing.key,
                           pairing.fingerprint)  # fmt: skip
        self.store.set_hue_bridge(bridge)
        self.breaker.success()
        self.lane.submit(self.poll)
        return bridge

    def reconnect(self) -> None:
        """Take over a new certificate of the paired bridge if its key still works."""
        bridge = self.store.current().hue.bridge
        if bridge is None:
            raise LightsUnavailable("hue_not_configured")

        def job() -> HueBridge:
            fingerprint = self.connector.fingerprint(bridge.ip)
            self.connector.client(bridge.ip, bridge.key, fingerprint).rooms()  # the key works
            return replace(bridge, fingerprint=fingerprint)

        self.store.set_hue_bridge(self._run(job))
        self.breaker.success()
        self.lane.submit(self.poll)

    def forget(self) -> None:
        self.store.set_hue_bridge(None)
        self.lane.submit(self.poll)

    def admin_view(self) -> dict[str, Any]:
        """Rooms and the chosen room's scenes, read fresh from the bridge."""
        hue = self.store.current().hue
        view: dict[str, Any] = {"available": self.available, "problem": self.problem}
        if hue.bridge is None:
            return view | {"rooms": [], "scenes": []}

        def job() -> tuple[list[Room], list[Scene]]:
            client = self._client_for(hue.bridge)
            rooms, scenes = client.rooms(), client.scenes()
            with self._lock:
                self._rooms, self._rooms_read_at = rooms, self.clock.monotonic()
            return rooms, scenes

        try:
            rooms, scenes = self._run(job)
        except LightsUnavailable as exc:
            return view | {"available": False, "problem": exc.code, "rooms": [], "scenes": []}
        view["available"], view["problem"] = True, None
        return view | {
            "rooms": [{"id": r.id, "name": r.name} for r in rooms if r.grouped_light],
            "scenes": [
                {"id": s.id, "name": s.name, "room": s.room} for s in scenes if s.room is not None
            ],
        }

    def status(self) -> dict[str, Any]:
        hue = self.store.current().hue
        return {
            "configured": hue.bridge is not None and hue.room is not None and bool(hue.slots),
            "bridge": hue.bridge.name if hue.bridge else None,
            "available": self.available,
            "problem": self.problem,
        }

    def _run(self, job: Callable[[], Any], timeout: float = 8.0) -> Any:
        """Run ``job`` on the light lane and wait (one bridge call at a time)."""
        future = self.lane.submit(job)
        try:
            return future.result(timeout)
        except FutureTimeout as exc:
            future.cancel()
            raise LightsUnavailable("hue_unreachable") from exc
        except HueError as exc:
            raise LightsUnavailable(exc.code) from exc
