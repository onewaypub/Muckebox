# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Short-lived time state, kept in ``DATA_DIR/timers.json``.

It holds what must survive a restart but is not a setting: the parents'
override, the kids' sleep timer and lock, which end of the usage time has
already been handled, the volume before a fade, today's game time, whether
a game muted the speaker and whether the lights were switched at the end
of the sleep timer.

Changes happen in memory (also from web requests); ``save()`` writes them
from the transport lane, never while a request waits and never under the
runtime's switch lock, because a sleeping NAS disk can take seconds.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from muckebox.storage import atomic_write

log = logging.getLogger(__name__)


@dataclass
class SleepTimer:
    ends_at: float
    #: Until when the tiles stay locked after ``ends_at``.
    lock_end: float
    #: Seconds of fading before ``ends_at``.
    fade: float


@dataclass
class TimerState:
    override: tuple[float, float] | None = None
    sleep: SleepTimer | None = None
    #: The end (a Phase.last_end) that has been handled: paused and restored.
    done_end: float | None = None
    #: The volume before the current fade began (restored after the pause, or
    #: when the fade stops early, e.g. because parents allowed more time).
    pre_fade: int | None = None
    #: Local date ("YYYY-MM-DD") and the game seconds used on it.
    games_date: str | None = None
    games_seconds: float = 0.0
    #: A game muted the speaker (unmute after a crash or restart).
    game_mute: bool = False
    #: The sleep timer end whose lights were handled (scene or off).
    lights_done_end: float | None = None


class TimersFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._version = 0
        self._saved_version = 0
        self.state = self._load()

    @contextlib.contextmanager
    def change(self) -> Iterator[TimerState]:
        """``with timers.change() as state: state.x = ...`` changes it and marks it for saving."""
        with self._lock:
            yield self.state
            self._version += 1

    @contextlib.contextmanager
    def read(self) -> Iterator[TimerState]:
        with self._lock:
            yield self.state

    def save(self) -> None:
        """Write the state if it changed (call from the transport lane)."""
        with self._lock:
            if self._version == self._saved_version:
                return
            version = self._version
            payload = json.dumps(_to_json(self.state)).encode("utf-8")
        try:
            atomic_write(self.path, payload)
        except OSError as exc:
            log.warning("Could not save %s: %s", self.path.name, exc)
        with self._lock:
            self._saved_version = max(self._saved_version, version)

    def _load(self) -> TimerState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return _from_json(data)
        except FileNotFoundError:
            return TimerState()
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            log.warning("Ignoring %s (%s)", self.path.name, exc)
            return TimerState()


def _to_json(state: TimerState) -> dict[str, Any]:
    data = asdict(state)
    data["override"] = list(state.override) if state.override else None
    return data


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"not a number: {value!r}")
    return float(value)  # type: ignore[arg-type]


def _from_json(data: object) -> TimerState:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    override = data.get("override")
    sleep = data.get("sleep")
    pre_fade = data.get("pre_fade")
    date = data.get("games_date")
    return TimerState(
        override=(_number(override[0]), _number(override[1])) if override else None,
        sleep=SleepTimer(
            _number(sleep["ends_at"]), _number(sleep["lock_end"]), _number(sleep["fade"])
        )
        if sleep
        else None,
        done_end=_number(data["done_end"]) if data.get("done_end") is not None else None,
        pre_fade=None if pre_fade is None else int(_number(pre_fade)),
        games_date=date if isinstance(date, str) else None,
        games_seconds=_number(data.get("games_seconds", 0.0)),
        game_mute=data.get("game_mute") is True,
        lights_done_end=_number(data["lights_done_end"])
        if data.get("lights_done_end") is not None
        else None,
    )
