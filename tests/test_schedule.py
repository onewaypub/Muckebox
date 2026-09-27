# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from muckebox.schedule import (
    FADE_FLOOR,
    Lock,
    Schedule,
    evaluate,
    extend_override,
    fade_factor,
    next_morning,
    parse_schedule,
    schedule_to_json,
)

BERLIN = ZoneInfo("Europe/Berlin")
WEEKDAYS = {day: {"from": "07:00", "to": "19:00"} for day in ("mon", "tue", "wed", "thu", "fri")}
WEEKEND = {"sat": {"from": "08:00", "to": "20:00"}, "sun": {"from": "08:00", "to": "19:30"}}


def at(text):
    """Local Berlin time -> epoch seconds. 2026-09-28 is a Monday."""
    return datetime.fromisoformat(text).replace(tzinfo=BERLIN).timestamp()


def local(epoch):
    return datetime.fromtimestamp(epoch, BERLIN).strftime("%a %Y-%m-%d %H:%M")


def schedule(days=None, fade=10):
    return parse_schedule(
        {"enabled": True, "fade_minutes": fade, "days": days or {**WEEKDAYS, **WEEKEND}}
    )


def test_disabled_schedule_is_off():
    assert evaluate(Schedule(), at("2026-09-28 23:00"), BERLIN).kind == "off"


def test_open_fading_and_closed():
    s = schedule()
    phase = evaluate(s, at("2026-09-28 12:00"), BERLIN)
    assert (phase.kind, local(phase.ends_at), local(phase.fade_from)) == (
        "open",
        "Mon 2026-09-28 19:00",
        "Mon 2026-09-28 18:50",
    )
    fading = evaluate(s, at("2026-09-28 18:55"), BERLIN)
    assert fading.kind == "fading"
    assert fade_factor(fading, at("2026-09-28 18:55")) == pytest.approx(1 - (1 - FADE_FLOOR) / 2)
    closed = evaluate(s, at("2026-09-28 19:05"), BERLIN)
    assert (closed.kind, local(closed.opens_at), local(closed.last_end)) == (
        "closed",
        "Tue 2026-09-29 07:00",
        "Mon 2026-09-28 19:00",
    )
    assert fade_factor(closed, at("2026-09-28 19:05")) == 1.0


def test_early_morning_is_closed_until_the_window():
    phase = evaluate(schedule(), at("2026-09-29 06:30"), BERLIN)
    assert (phase.kind, local(phase.opens_at)) == ("closed", "Tue 2026-09-29 07:00")
    assert local(phase.last_end) == "Mon 2026-09-28 19:00"  # the key of the last end


def test_week_wraps_from_sunday_to_monday():
    phase = evaluate(schedule(), at("2026-10-04 21:00"), BERLIN)  # a Sunday
    assert local(phase.opens_at) == "Mon 2026-10-05 07:00"


def test_a_day_without_window_is_free():
    s = schedule({**WEEKDAYS})  # no windows at the weekend
    friday_night = evaluate(s, at("2026-10-02 20:00"), BERLIN)
    assert local(friday_night.opens_at) == "Sat 2026-10-03 00:00"
    saturday = evaluate(s, at("2026-10-03 23:30"), BERLIN)
    assert saturday.kind == "open"
    # Saturday and Sunday are free; Monday is limited again from midnight.
    assert local(saturday.ends_at) == "Mon 2026-10-05 00:00"


def test_window_until_midnight():
    s = schedule({"mon": {"from": "07:00", "to": "24:00"}, "tue": {"from": "07:00", "to": "19:00"}})
    assert local(evaluate(s, at("2026-09-28 23:00"), BERLIN).ends_at) == "Tue 2026-09-29 00:00"


def test_override_after_the_end():
    s = schedule()
    now = at("2026-09-28 19:05")
    override = extend_override(s, now, BERLIN, None, minutes=15)
    phase = evaluate(s, now, BERLIN, override)
    assert (phase.kind, local(phase.ends_at)) == ("open", "Mon 2026-09-28 19:20")
    assert evaluate(s, at("2026-09-28 19:12"), BERLIN, override).kind == "fading"
    after = evaluate(s, at("2026-09-28 19:25"), BERLIN, override)
    assert (after.kind, local(after.last_end)) == ("closed", "Mon 2026-09-28 19:20")


def test_override_during_the_fade_counts_from_now():
    s = schedule()
    now = at("2026-09-28 18:55")
    override = extend_override(s, now, BERLIN, None, minutes=15)
    assert local(evaluate(s, now, BERLIN, override).ends_at) == "Mon 2026-09-28 19:10"
    again = extend_override(s, at("2026-09-28 18:56"), BERLIN, override, minutes=15)
    assert local(again[1]) == "Mon 2026-09-28 19:25"  # twice: 30 minutes


def test_override_until_morning():
    s = schedule()
    override = extend_override(s, at("2026-09-28 20:00"), BERLIN, None, morning=True)
    assert local(override[1]) == "Tue 2026-09-29 07:00"
    phase = evaluate(s, at("2026-09-29 03:00"), BERLIN, override)
    assert phase.kind == "open"
    assert local(phase.ends_at) == "Tue 2026-09-29 19:00"  # merged with Tuesday's window


def test_override_needs_minutes():
    with pytest.raises(ValueError):
        extend_override(schedule(), 0.0, BERLIN, None, minutes=0)


def test_next_morning_uses_the_wake_time_without_a_window():
    assert local(next_morning(Schedule(), at("2026-09-28 20:00"), BERLIN)) == "Tue 2026-09-29 07:00"
    late = next_morning(Schedule(), at("2026-09-28 20:00"), BERLIN, wake=time(6, 30))
    assert local(late) == "Tue 2026-09-29 06:30"
    s = schedule({**WEEKDAYS})
    assert local(next_morning(s, at("2026-10-02 20:00"), BERLIN)) == "Sat 2026-10-03 07:00"


def test_sleep_lock_without_a_schedule():
    lock = Lock(start=at("2026-09-28 20:00"), end=at("2026-09-29 07:00"), fade=300)
    assert evaluate(Schedule(), at("2026-09-28 19:50"), BERLIN, lock=lock).kind == "open"
    fading = evaluate(Schedule(), at("2026-09-28 19:57"), BERLIN, lock=lock)
    assert (fading.kind, local(fading.fade_from)) == ("fading", "Mon 2026-09-28 19:55")
    closed = evaluate(Schedule(), at("2026-09-28 22:00"), BERLIN, lock=lock)
    assert (closed.kind, local(closed.opens_at), local(closed.last_end)) == (
        "closed",
        "Tue 2026-09-29 07:00",
        "Mon 2026-09-28 20:00",
    )
    assert evaluate(Schedule(), at("2026-09-29 07:30"), BERLIN, lock=lock).kind == "open"


def test_sleep_lock_inside_a_window():
    lock = Lock(start=at("2026-09-28 16:00"), end=at("2026-09-29 07:00"), fade=300)
    phase = evaluate(schedule(), at("2026-09-28 17:00"), BERLIN, lock=lock)
    assert (phase.kind, local(phase.opens_at)) == ("closed", "Tue 2026-09-29 07:00")


@pytest.mark.parametrize("day", ["2026-03-29", "2026-10-25"])  # daylight saving changes
def test_daylight_saving_days(day):
    s = schedule({"sun": {"from": "07:00", "to": "19:00"}})
    phase = evaluate(s, at(f"{day} 12:00"), BERLIN)
    assert local(phase.ends_at) == datetime.fromisoformat(f"{day} 19:00").strftime(
        "%a %Y-%m-%d %H:%M"
    )
    assert phase.ends_at - phase.fade_from == 600


def test_round_trip():
    data = {
        "enabled": True,
        "fade_minutes": 5,
        "days": {
            "mon": {"from": "07:00", "to": "19:00"},
            "tue": None,
            "wed": {"from": "06:30", "to": "24:00"},
            "thu": None,
            "fri": None,
            "sat": None,
            "sun": None,
        },
    }
    assert schedule_to_json(parse_schedule(data)) == data


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"enabled": "yes"},
        {"fade_minutes": 31},
        {"fade_minutes": 2.5},
        {"days": {"monday": None}},
        {"days": {"mon": {"from": "19:00", "to": "07:00"}}},
        {"days": {"mon": {"from": "07:00", "to": "07:00"}}},
        {"days": {"mon": {"from": "24:00", "to": "24:00"}}},
        {"days": {"mon": {"from": "7:00", "to": "19:00"}}},
        {"days": {"mon": {"from": "07:00"}}},
        {"days": {"mon": "07:00-19:00"}},
    ],
)
def test_invalid_schedules(data):
    with pytest.raises(ValueError):
        parse_schedule(data)
