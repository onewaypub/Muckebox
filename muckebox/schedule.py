# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""When the kids may use Muckebox: pure time logic, no files, no network.

Parents set one window per weekday (e.g. 07:00-19:00). A day without a
window is free. On top of that:

* an **override** from the parents is an extra allowed interval;
* the kids' **sleep lock** (after the sleep timer ran out) is an interval in
  which nothing is allowed, whatever the windows say.

Everything is computed on absolute times (epoch seconds). Window bounds are
built per local calendar day with the zone's rules, so daylight saving
changes are handled correctly.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_FADE_MINUTES = 10
MAX_FADE_MINUTES = 30
#: At the end of a fade the volume is down to this share of where it started.
FADE_FLOOR = 0.2
#: Days around today that are looked at (overrides last at most until morning).
HORIZON_DAYS = 8
DEFAULT_WAKE = time(7, 0)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_INF = math.inf


class WindowOrderError(ValueError):
    """A window whose end is not after its start (e.g. across midnight)."""


@dataclass(frozen=True)
class Window:
    start: time
    #: None means midnight at the end of the day ("24:00").
    end: time | None


@dataclass(frozen=True)
class Schedule:
    enabled: bool = False
    fade_minutes: int = DEFAULT_FADE_MINUTES
    #: One entry per weekday, Monday first; None = no limit that day.
    days: tuple[Window | None, ...] = field(default_factory=lambda: (None,) * 7)


@dataclass(frozen=True)
class Lock:
    """Nothing is allowed from ``start`` to ``end``; the fade before it lasts ``fade``."""

    start: float
    end: float
    fade: float


@dataclass(frozen=True)
class Phase:
    #: "off" (no limits at all), "open", "fading" or "closed".
    kind: str
    #: End of the current allowed period (None: open-ended).
    ends_at: float | None = None
    #: Start of the next allowed period while closed (None: not within the horizon).
    opens_at: float | None = None
    #: When the fade before ``ends_at`` begins.
    fade_from: float | None = None
    #: The latest end of an allowed period at or before now: the key that makes
    #: Muckebox pause exactly once per end.
    last_end: float | None = None

    @property
    def allowed(self) -> bool:
        return self.kind != "closed"


# -- parsing ------------------------------------------------------------------------


def parse_time(value: object, *, end: bool = False) -> time | None:
    """ "HH:MM" -> time. As an end, "24:00" means midnight at the end of the day (None)."""
    if end and value == "24:00":
        return None
    if not isinstance(value, str) or not (match := _TIME_RE.match(value)):
        raise ValueError(f"invalid time {value!r}")
    return time(int(match.group(1)), int(match.group(2)))


def format_time(value: time | None) -> str:
    return "24:00" if value is None else value.strftime("%H:%M")


def parse_schedule(data: object) -> Schedule:
    """Build a schedule from its JSON form; raises ValueError if anything is off."""
    if data is None:
        return Schedule()
    if not isinstance(data, dict):
        raise ValueError("schedule must be an object")
    enabled = data.get("enabled", False)
    fade = data.get("fade_minutes", DEFAULT_FADE_MINUTES)
    days = data.get("days", {})
    if type(enabled) is not bool:
        raise ValueError("enabled must be true or false")
    if type(fade) is not int or not 0 <= fade <= MAX_FADE_MINUTES:
        raise ValueError("fade_minutes out of range")
    if not isinstance(days, dict) or set(days) - set(DAYS):
        raise ValueError("days must name weekdays")
    windows = []
    for name in DAYS:
        entry = days.get(name)
        if entry is None:
            windows.append(None)
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: expected from/to")
        start = parse_time(entry.get("from"))
        end = parse_time(entry.get("to"), end=True)
        if start is None or (end is not None and end <= start):
            raise WindowOrderError(f"{name}: 'to' must be after 'from' on the same day")
        windows.append(Window(start, end))
    return Schedule(enabled, fade, tuple(windows))


def schedule_to_json(schedule: Schedule) -> dict[str, Any]:
    return {
        "enabled": schedule.enabled,
        "fade_minutes": schedule.fade_minutes,
        "days": {
            name: None
            if window is None
            else {"from": format_time(window.start), "to": format_time(window.end)}
            for name, window in zip(DAYS, schedule.days, strict=True)
        },
    }


# -- evaluation ---------------------------------------------------------------------


def evaluate(
    schedule: Schedule,
    now: float,
    zone: tzinfo,
    override: tuple[float, float] | None = None,
    lock: Lock | None = None,
) -> Phase:
    """Where ``now`` lies: open, fading before an end, or closed."""
    if not schedule.enabled and lock is None:
        return Phase("off")
    periods = _allowed_periods(schedule, now, zone, override, lock)
    last_end = max((end for _, end in periods if end <= now), default=None)
    for start, end in periods:
        if start <= now < end:
            ends_at = None if end == _INF else end
            fade = _fade_seconds(schedule, end, lock)
            fade_from = None if ends_at is None or fade <= 0 else max(start, end - fade)
            kind = "fading" if fade_from is not None and now >= fade_from else "open"
            return Phase(kind, ends_at=ends_at, fade_from=fade_from, last_end=last_end)
    opens_at = min((start for start, _ in periods if start > now), default=None)
    return Phase("closed", opens_at=opens_at, last_end=last_end)


def fade_factor(phase: Phase, now: float) -> float:
    """1.0 while open; falls linearly to FADE_FLOOR during the fade."""
    if phase.kind != "fading" or phase.fade_from is None or phase.ends_at is None:
        return 1.0
    span = phase.ends_at - phase.fade_from
    progress = min(1.0, max(0.0, (now - phase.fade_from) / span)) if span > 0 else 1.0
    return 1.0 - (1.0 - FADE_FLOOR) * progress


def next_morning(
    schedule: Schedule, after: float, zone: tzinfo, wake: time = DEFAULT_WAKE
) -> float:
    """When the kids may start again after ``after``: the next window start,
    or the wake time on days without a window (or without a schedule)."""
    day = datetime.fromtimestamp(after, zone).date()
    for offset in range(HORIZON_DAYS + 1):
        current = day + timedelta(days=offset)
        window = schedule.days[current.weekday()] if schedule.enabled else None
        start = _at(current, window.start if window else wake, zone)
        if start > after:
            return start
    return after + 86400.0


def extend_override(
    schedule: Schedule,
    now: float,
    zone: tzinfo,
    current: tuple[float, float] | None,
    *,
    minutes: int | None = None,
    morning: bool = False,
    wake: time = DEFAULT_WAKE,
) -> tuple[float, float]:
    """The new override interval for "+minutes" or "until tomorrow morning".

    "+15" counts from now, or from the end of an override that is still
    running, so pressing it twice gives 30 minutes.
    """
    if morning:
        return (now, next_morning(schedule, now, zone, wake))
    if minutes is None or minutes <= 0:
        raise ValueError("minutes must be positive")
    base = current[1] if current and current[1] > now else now
    return (now, base + minutes * 60.0)


# -- helpers ------------------------------------------------------------------------


def _at(day: date, clock: time | None, zone: tzinfo) -> float:
    if clock is None:  # "24:00": the start of the next day
        return _at(day + timedelta(days=1), time(0, 0), zone)
    return datetime.combine(day, clock, tzinfo=zone).timestamp()


def _window_periods(schedule: Schedule, now: float, zone: tzinfo) -> list[tuple[float, float]]:
    if not schedule.enabled:
        return [(-_INF, _INF)]
    today = datetime.fromtimestamp(now, zone).date()
    periods = []
    for offset in range(-1, HORIZON_DAYS + 1):
        day = today + timedelta(days=offset)
        window = schedule.days[day.weekday()]
        if window is None:  # a free day
            periods.append((_at(day, time(0, 0), zone), _at(day, None, zone)))
        else:
            periods.append((_at(day, window.start, zone), _at(day, window.end, zone)))
    # Free days before and after the horizon: keep the ends open, so that a
    # free day at the edge is not reported as an end.
    first, last = periods[0], periods[-1]
    if schedule.days[(today + timedelta(days=-1)).weekday()] is None:
        periods[0] = (-_INF, first[1])
    if schedule.days[(today + timedelta(days=HORIZON_DAYS)).weekday()] is None:
        periods[-1] = (last[0], _INF)
    return periods


def _allowed_periods(
    schedule: Schedule,
    now: float,
    zone: tzinfo,
    override: tuple[float, float] | None,
    lock: Lock | None,
) -> list[tuple[float, float]]:
    periods = _window_periods(schedule, now, zone)
    if override is not None and override[1] > override[0]:
        periods.append(override)
    merged = _merge(periods)
    if lock is not None:
        merged = _subtract(merged, (lock.start, lock.end))
    return merged


def _merge(periods: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for start, end in sorted(periods):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _subtract(
    periods: list[tuple[float, float]], gap: tuple[float, float]
) -> list[tuple[float, float]]:
    result = []
    for start, end in periods:
        if end <= gap[0] or start >= gap[1]:
            result.append((start, end))
            continue
        if start < gap[0]:
            result.append((start, gap[0]))
        if end > gap[1]:
            result.append((gap[1], end))
    return result


def _fade_seconds(schedule: Schedule, end: float, lock: Lock | None) -> float:
    if lock is not None and end == lock.start:
        return lock.fade
    return schedule.fade_minutes * 60.0
