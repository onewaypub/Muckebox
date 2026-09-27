# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Applies the usage times, the parents' override and the kids' sleep timer.

The time logic itself is pure (:mod:`muckebox.schedule`); this class feeds
it with the settings, the stored timers and the clock, and answers the
runtime's questions: may this command run now, how loud may it be during
the fade, and is there an end that still needs a pause.
"""

from __future__ import annotations

import logging
from typing import Any

from muckebox.localtime import Zone, ZoneResolver, local_time
from muckebox.schedule import Lock, Phase, evaluate, extend_override, fade_factor, next_morning
from muckebox.settings import SettingsStore

from .clock import Clock
from .timers import SleepTimer, TimersFile

log = logging.getLogger(__name__)

#: After an end, Muckebox still pauses for this long (e.g. after a restart).
#: Later it never pauses, so playback started from the Sonos app stays alone.
PAUSE_GRACE = 15 * 60
#: Commands that stay possible when the usage time is over.
ALLOWED_WHEN_CLOSED = frozenset({"pause", "volume_down", "favorites"})


class Refused(Exception):
    """A command is not allowed right now (e.g. bedtime); not a speaker problem."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TimeKeeper:
    def __init__(
        self, store: SettingsStore, timers: TimersFile, clock: Clock, zones: ZoneResolver
    ) -> None:
        self.store = store
        self.timers = timers
        self.clock = clock
        self.zones = zones

    def zone(self) -> Zone:
        return self.zones.zone(self.store.current().time_zone)

    # -- where are we ----------------------------------------------------------

    def phase(self, now: float | None = None) -> Phase:
        now = self.clock.time() if now is None else now
        settings = self.store.current()
        with self.timers.read() as state:
            override, sleep = state.override, state.sleep
        lock = None
        if sleep is not None and sleep.lock_end > now:
            lock = Lock(sleep.ends_at, sleep.lock_end, sleep.fade)
        return evaluate(settings.schedule, now, self.zone().tz, override, lock)

    def check(self, command: str) -> None:
        """Raise :class:`Refused` if ``command`` is not allowed now."""
        if command not in ALLOWED_WHEN_CLOSED and not self.phase().allowed:
            raise Refused("bedtime")

    # -- fading and pausing ----------------------------------------------------

    def soft_limit(self, observed: int) -> int | None:
        """The volume limit during a fade, starting from the volume at its start."""
        now = self.clock.time()
        phase = self.phase(now)
        if phase.kind != "fading":
            return None
        key = _key(phase.ends_at)
        with self.timers.read() as state:
            base = state.fade_base.get(key)
        if base is None:
            base = observed
            with self.timers.change() as state:
                state.fade_base = {key: base}  # only the current fade matters
        return max(1, round(base * fade_factor(phase, now)))

    def current_limit(self) -> int | None:
        """The fade limit right now, if a fade has started (no speaker access)."""
        now = self.clock.time()
        phase = self.phase(now)
        if phase.kind != "fading":
            return None
        with self.timers.read() as state:
            base = state.fade_base.get(_key(phase.ends_at))
        return None if base is None else max(1, round(base * fade_factor(phase, now)))

    def due_pause(self) -> float | None:
        """The end that still needs a pause, or None."""
        now = self.clock.time()
        phase = self.phase(now)
        if phase.kind != "closed" or phase.last_end is None:
            return None
        if now - phase.last_end > PAUSE_GRACE:
            return None
        with self.timers.read() as state:
            done = state.done_end
        if done is not None and done >= phase.last_end:
            return None
        return phase.last_end

    def mark_done(self, end: float) -> int | None:
        """Remember that ``end`` is handled; returns the volume before its fade."""
        with self.timers.change() as state:
            state.done_end = end
            return state.fade_base.pop(_key(end), None)

    # -- the parents' override -------------------------------------------------

    def override(self, *, minutes: int | None = None, morning: bool = False) -> Phase:
        now = self.clock.time()
        settings = self.store.current()
        with self.timers.read() as state:
            sleeping = state.sleep is not None and state.sleep.lock_end > now
        if not settings.schedule.enabled and not sleeping:
            raise Refused("schedule_off")
        with self.timers.change() as state:
            state.override = extend_override(
                settings.schedule,
                now,
                self.zone().tz,
                state.override,
                minutes=minutes,
                morning=morning,
                wake=settings.sleep_timer.wake,
            )
            state.sleep = None  # a parent's release also ends the kids' sleep lock
        log.info("Override until %s", self._local(state.override[1]))
        return self.phase(now)

    def end_override(self) -> Phase:
        now = self.clock.time()
        with self.timers.change() as state:
            if state.override and state.override[1] > now:
                state.override = (state.override[0], now)
        return self.phase(now)

    # -- the kids' sleep timer ---------------------------------------------------

    def start_sleep_timer(self) -> SleepTimer:
        now = self.clock.time()
        settings = self.store.current()
        timer = settings.sleep_timer
        if not timer.enabled:
            raise Refused("sleep_timer_off")
        if not self.phase(now).allowed:
            raise Refused("bedtime")
        ends_at = now + timer.minutes * 60
        fade = min(settings.schedule.fade_minutes * 60, timer.minutes * 30)
        lock_end = next_morning(settings.schedule, ends_at, self.zone().tz, timer.wake)
        sleep = SleepTimer(ends_at=ends_at, lock_end=lock_end, fade=fade)
        with self.timers.change() as state:
            state.sleep = sleep
        log.info("Sleep timer until %s", self._local(ends_at))
        return sleep

    def cancel_sleep_timer(self) -> None:
        """Stop a running sleep timer; once it has run out, only an override helps."""
        now = self.clock.time()
        with self.timers.change() as state:
            if state.sleep is not None and state.sleep.ends_at > now:
                state.sleep = None

    # -- housekeeping and state ------------------------------------------------

    def tidy(self) -> None:
        """Drop timers that are long over (keeps timers.json small)."""
        now = self.clock.time()
        with self.timers.read() as state:
            stale_sleep = state.sleep is not None and state.sleep.lock_end < now
            stale_override = state.override is not None and state.override[1] < now - PAUSE_GRACE
        if stale_sleep or stale_override:
            with self.timers.change() as state:
                if stale_sleep:
                    state.sleep = None
                if stale_override:
                    state.override = None

    def document(self) -> dict[str, Any]:
        """For /api/state: only values that change at transitions (stable ETag)."""
        now = self.clock.time()
        phase = self.phase(now)
        settings = self.store.current()
        with self.timers.read() as state:
            override, sleep = state.override, state.sleep
        return {
            "schedule": {
                "phase": phase.kind,
                "ends_at": _epoch(phase.ends_at),
                "opens_at": _epoch(phase.opens_at),
                "fade_from": _epoch(phase.fade_from),
                "override_until": _epoch(override[1]) if override and override[1] > now else None,
            },
            "sleep_timer": {
                "enabled": settings.sleep_timer.enabled,
                "minutes": settings.sleep_timer.minutes,
                "ends_at": _epoch(sleep.ends_at) if sleep and sleep.ends_at > now else None,
            },
        }

    def _local(self, epoch: float) -> str:
        return local_time(epoch, self.zone()).strftime("%a %H:%M")


def _key(end: float | None) -> str:
    return f"{end:.0f}"


def _epoch(value: float | None) -> int | None:
    return None if value is None else int(value)
