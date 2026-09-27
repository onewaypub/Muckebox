# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep the kids room at or below the maximum volume."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable

from muckebox.sonos.backend import SonosBackend

from .clock import Clock

log = logging.getLogger(__name__)

FIGHT_WINDOW = 10.0  # seconds
# Corrections within the window that count as a fight. The guard polls about
# once per second, so at most ~10 corrections fit into the window.
FIGHT_CORRECTIONS = 5


class VolumeGuard:
    """Reads the room player's volume and sets it back if it is too high.

    ``step()`` runs about once per second on the volume lane. The limit is
    read from ``max_volume()`` on every step, so it applies at once when the
    parents change it.

    ``soft_limit(volume)`` may lower it further for a while (the fade before
    the end of the usage time). Lowering to a soft limit is not a correction:
    it is neither counted nor logged as one.
    """

    def __init__(
        self,
        backend: SonosBackend,
        max_volume: Callable[[], int],
        clock: Clock,
        soft_limit: Callable[[int], int | None] | None = None,
    ) -> None:
        self._backend = backend
        self._max_volume = max_volume
        self._soft_limit = soft_limit
        self._clock = clock
        self._recent: deque[float] = deque()
        self.corrections = 0
        self.fighting = False
        self.last_volume: int | None = None

    def step(self) -> int:
        """Check the volume once; correct it if needed; return the volume now."""
        volume = self._backend.get_volume()
        return self.on_volume_observed(volume)

    def on_volume_observed(self, volume: int) -> int:
        """Handle a volume reading from any source (poll now, events later)."""
        hard = self._max_volume()
        limit = self._limit(volume, hard)
        if volume > limit:
            self._backend.set_volume(limit)
            if volume > hard:
                self._record_correction(volume, hard)
            else:
                log.debug("Fading: volume %d lowered to %d", volume, limit)
            volume = limit
        else:
            self._update_fight(self._clock.monotonic())
        self.last_volume = volume
        return volume

    def _limit(self, volume: int, hard: int) -> int:
        soft = self._soft_limit(volume) if self._soft_limit else None
        return hard if soft is None else min(hard, soft)

    def change(self, direction: str, step: int) -> int:
        """Louder/quieter from the kids view: absolute, clamped, never over the limit."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        current = self._backend.get_volume()
        limit = self._limit(current, self._max_volume())
        target = current + step if direction == "up" else current - step
        target = max(0, min(limit, target))
        if target != current:
            self._backend.set_volume(target)
        self.last_volume = target
        return target

    def _update_fight(self, now: float) -> bool:
        while self._recent and now - self._recent[0] > FIGHT_WINDOW:
            self._recent.popleft()
        self.fighting = len(self._recent) >= FIGHT_CORRECTIONS
        return self.fighting

    def _record_correction(self, observed: int, limit: int) -> None:
        now = self._clock.monotonic()
        self.corrections += 1
        self._recent.append(now)
        was_fighting = self.fighting
        fighting = self._update_fight(now)
        if fighting and not was_fighting:
            log.warning(
                "Volume keeps being raised above the limit (%d corrections in %.0f s); "
                "still enforcing %d",
                len(self._recent),
                FIGHT_WINDOW,
                limit,
            )
        log.info("Volume %d was above the limit; set back to %d", observed, limit)
