# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time source, injectable so that tests control time."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_790_000_000.0 + self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
