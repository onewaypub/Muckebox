# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The games: which may run now, the daily game time, the freeze dance's mute.

The games themselves run on the tablet. The server decides whether one may
start, grants it a round (at most what is left today and never into the
fade before bedtime), books that time and gives back what was not used.

The breathing exercise does not count and needs no server; it is allowed
at bedtime too, because helping kids fall asleep is its purpose.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from muckebox.localtime import local_time
from muckebox.settings import GAMES, SettingsStore

from .clock import Clock
from .timekeeper import Refused, TimeKeeper
from .timers import TimersFile

log = logging.getLogger(__name__)

#: Games that count against the daily game time.
COUNTED = ("freeze_dance", "sound_quiz", "move_like")
#: Length of a round per level (seconds): small, middle, big kids.
ROUND_SECONDS = {
    "freeze_dance": (120, 180, 240),
    "sound_quiz": (180, 240, 300),
    "move_like": (150, 180, 240),
}
#: A game needs at least this long, or it does not start.
MIN_GRANT = 60
#: How long a mute from the freeze dance lasts without being renewed.
MUTE_LEASE = 12.0
#: A game that ran out can still be ended this long afterwards.
END_GRACE = 60.0


class UnknownGame(Exception):
    pass


@dataclass
class ActiveGame:
    id: str
    level: int
    started_at: float
    granted: float

    @property
    def ends_at(self) -> float:
        return self.started_at + self.granted


class Games:
    def __init__(
        self, store: SettingsStore, timers: TimersFile, keeper: TimeKeeper, clock: Clock
    ) -> None:
        self.store = store
        self.timers = timers
        self.keeper = keeper
        self.clock = clock
        self._lock = threading.Lock()
        self._active: ActiveGame | None = None
        #: A game that ran out; the tablet may still end it for a short while
        #: (e.g. the freeze dance, whose end also stops its music).
        self._expired: ActiveGame | None = None
        #: Monotonic time until which the freeze dance may keep the speaker muted.
        self.mute_until: float | None = None

    # -- the day's game time -------------------------------------------------

    def used_today(self) -> float:
        today = self._today()
        with self.timers.read() as state:
            return state.games_seconds if state.games_date == today else 0.0

    def remaining(self) -> float:
        limit = self.store.current().games.daily_minutes * 60
        return max(0.0, limit - self.used_today())

    def _book(self, seconds: float) -> None:
        today = self._today()
        with self.timers.change() as state:
            if state.games_date != today:
                state.games_date, state.games_seconds = today, 0.0
            state.games_seconds = max(0.0, state.games_seconds + seconds)

    def _today(self) -> str:
        return local_time(self.clock.time(), self.keeper.zone()).date().isoformat()

    # -- starting and ending -----------------------------------------------------

    def active(self) -> ActiveGame | None:
        with self._lock:
            game = self._active
            if game is not None and self.clock.time() >= game.ends_at:
                self._expired, self._active, game = game, None, None
            return game

    def check(self, game_id: str) -> tuple[int, float]:
        """Level and granted seconds if ``game_id`` may start now; raises otherwise."""
        if game_id not in GAMES:
            raise UnknownGame(game_id)
        setting = self.store.current().games.items[game_id]
        if not setting.enabled:
            raise Refused("game_unavailable")
        if game_id not in COUNTED:
            return setting.level, 0.0
        if self.active() is not None:
            raise Refused("game_running")
        return setting.level, self._grant(game_id, setting.level)

    def _grant(self, game_id: str, level: int) -> float:
        now = self.clock.time()
        phase = self.keeper.phase(now)
        if not phase.allowed:
            raise Refused("bedtime")
        remaining = self.remaining()
        if remaining < MIN_GRANT:
            raise Refused("games_limit_reached")
        grant = min(ROUND_SECONDS[game_id][level - 1], remaining)
        # Games end before the music starts to fade for bedtime.
        stop = phase.fade_from if phase.fade_from is not None else phase.ends_at
        if stop is not None:
            grant = min(grant, stop - now)
        if grant < MIN_GRANT:
            raise Refused("game_unavailable")
        return grant

    def start(self, game_id: str) -> ActiveGame:
        level, grant = self.check(game_id)
        game = ActiveGame(game_id, level, self.clock.time(), grant)
        if game_id in COUNTED:
            with self._lock:
                self._active = game
            self._book(grant)
            log.info("Game %s started (%d s)", game_id, grant)
        return game

    def end(self) -> ActiveGame | None:
        """End the running game (or one that ran out a moment ago); the time
        it did not use is given back."""
        now = self.clock.time()
        with self._lock:
            game, self._active = self._active, None
            expired, self._expired = self._expired, None
            if game is None and expired is not None and now <= expired.ends_at + END_GRACE:
                game = expired
        if game is None:
            return None
        unused = game.ends_at - max(self.clock.time(), game.started_at)
        if unused > 0:
            self._book(-unused)
        self.mute_until = None
        return game

    # -- state ------------------------------------------------------------------

    def document(self) -> dict[str, Any]:
        """For /api/state; changes only when a game starts or ends, or at bedtime."""
        settings = self.store.current().games
        remaining = int(self.remaining())
        active = self.active()
        items = []
        for game_id in GAMES:
            setting = settings.items[game_id]
            if not setting.enabled:
                continue
            available = True
            if game_id in COUNTED:
                # The same rule as starting (incl. "not into the bedtime fade"),
                # so the tablet never offers a game that would be refused.
                try:
                    self._grant(game_id, setting.level)
                except Refused:
                    available = False
                available = available and active is None
            items.append({"id": game_id, "level": setting.level, "available": available})
        return {
            "remaining": remaining,
            "items": items,
            "active": None if active is None else {"id": active.id, "ends_at": int(active.ends_at)},
        }
