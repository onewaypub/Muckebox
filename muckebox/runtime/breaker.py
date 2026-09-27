# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Circuit breaker: stop hammering a speaker that cannot be reached."""

from __future__ import annotations

import math
import threading

from .clock import Clock


class CircuitBreaker:
    """After a failure, refuse calls for a cooldown that doubles each time.

    While the breaker is open, callers fail fast instead of waiting for a
    network timeout. When the cooldown has passed, one call is allowed
    through as a probe; its success closes the breaker again.
    """

    def __init__(self, clock: Clock, min_cooldown: float, max_cooldown: float) -> None:
        self._clock = clock
        self._min = min_cooldown
        self._max = max_cooldown
        self._cooldown = 0.0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return self._clock.monotonic() >= self._open_until

    @property
    def is_open(self) -> bool:
        return not self.allow()

    @property
    def failing(self) -> bool:
        """True from the first failure until the next success."""
        return self._cooldown > 0

    def retry_in(self) -> int | None:
        with self._lock:
            remaining = self._open_until - self._clock.monotonic()
        return math.ceil(remaining) if remaining > 0 else None

    def success(self) -> None:
        with self._lock:
            self._cooldown = 0.0
            self._open_until = 0.0

    def failure(self) -> None:
        with self._lock:
            self._cooldown = min(self._max, self._cooldown * 2 or self._min)
            self._open_until = self._clock.monotonic() + self._cooldown
