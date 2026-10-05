# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Touch diagnosis: what the kids tablet receives when a child presses it.

Parents switch it on for a while on their page; meanwhile the tablet shows
every touch and sends what arrived (pointer, touch, click and long-press
events, and what the tiles made of them). Kept in memory only, never
written to disk, and never more than MAX_EVENTS entries. Every entry is
checked: only known names, numbers and short words are kept.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from typing import Any

from .clock import Clock

DURATION = 30 * 60  # seconds the diagnosis runs once switched on
MAX_EVENTS = 3000
MAX_BATCH = 200
#: Event types the tablet may send.
TYPES = frozenset(
    {
        "pointerdown", "pointermove", "pointerup", "pointercancel", "lostpointercapture",
        "touchstart", "touchend", "touchcancel", "contextmenu", "click",
        "tile-hold", "tile-release", "press-moved", "press-swipe", "press-cancel",
        "light", "playing", "page",
    }
)  # fmt: skip
_WORD_RE = re.compile(r"^[a-z0-9:_-]{1,40}$")
_NUMBERS = ("t", "x", "y", "id", "moved", "ms", "w", "h", "touches")
_WORDS = ("pt", "target")


class TouchLog:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until: float | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)

    def active(self) -> bool:
        with self._lock:
            return self._until is not None and self._clock.monotonic() < self._until

    def remaining(self) -> int:
        with self._lock:
            if self._until is None:
                return 0
            return max(0, int(self._until - self._clock.monotonic()))

    def start(self) -> None:
        with self._lock:
            self._until = self._clock.monotonic() + DURATION

    def stop(self) -> None:
        with self._lock:
            self._until = None

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def add(self, batch: object) -> int:
        """Keep the valid entries of ``batch``; returns how many were kept."""
        if not self.active():
            raise PermissionError("diagnosis is off")
        if not isinstance(batch, list) or len(batch) > MAX_BATCH:
            raise ValueError("bad batch")
        kept = [entry for entry in map(_clean, batch) if entry is not None]
        with self._lock:
            self._events.extend(kept)
        return len(kept)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)


def _clean(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("type") not in TYPES:
        return None
    result: dict[str, Any] = {"type": entry["type"]}
    for key in _NUMBERS:
        value = entry.get(key)
        if type(value) in (int, float) and abs(value) < 1e9:
            result[key] = round(value, 1)
    for key in _WORDS:
        value = entry.get(key)
        if isinstance(value, str) and _WORD_RE.match(value):
            result[key] = value
    return result
