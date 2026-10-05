# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The "anti disco" lock: after a tap, the same kind of tap waits a moment.

Small children love to hammer a button. Without this, twenty taps switch
tiles or skip songs every second. Taps are grouped (tiles, next/previous,
play/pause); a group stays closed for a few seconds after an accepted tap.
"""

from __future__ import annotations

import math
import threading

from .clock import Clock

#: Groups of kids' taps.
TILE = "tile"
SKIP = "skip"
TOGGLE = "toggle"
LIGHT = "light"
#: Fixed waits for the transport buttons; the tile wait is a setting.
SKIP_SECONDS = 3
TOGGLE_SECONDS = 1
LIGHT_SECONDS = 3


class CoolingDown(Exception):
    """The same kind of tap was accepted a moment ago."""

    def __init__(self, retry_in: int) -> None:
        super().__init__("cooling_down")
        self.code = "cooling_down"
        self.retry_in = retry_in


class Cooldown:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}

    def check(self, group: str) -> None:
        """Raise :class:`CoolingDown` while ``group`` waits."""
        with self._lock:
            left = self._until.get(group, 0.0) - self._clock.monotonic()
        if left > 0:
            raise CoolingDown(max(1, math.ceil(left)))

    def arm(self, group: str, seconds: float) -> None:
        """A tap of ``group`` was accepted: close the group for ``seconds``."""
        if seconds <= 0:
            return
        with self._lock:
            self._until[group] = self._clock.monotonic() + seconds
