# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.runtime.clock import FakeClock
from muckebox.runtime.touchlog import DURATION, MAX_BATCH, MAX_EVENTS, TouchLog


def test_only_while_switched_on_and_for_half_an_hour():
    clock = FakeClock()
    log = TouchLog(clock)
    with pytest.raises(PermissionError):
        log.add([{"type": "pointerdown"}])
    log.start()
    assert log.remaining() == DURATION
    assert log.add([{"type": "pointerdown", "x": 10.04, "pt": "touch"}]) == 1
    clock.advance(DURATION)
    assert not log.active()
    with pytest.raises(PermissionError):
        log.add([{"type": "pointerup"}])
    assert log.events() == [{"type": "pointerdown", "x": 10.0, "pt": "touch"}]


def test_entries_are_checked():
    log = TouchLog(FakeClock())
    log.start()
    batch = [
        {"type": "evil"},
        "nope",
        {"type": "click", "x": "1", "target": "<script>", "extra": 1, "pt": "pen"},
        {"type": "tile-hold", "ms": 250, "target": "tile:3", "moved": 2},
    ]
    assert log.add(batch) == 2
    assert log.events() == [
        {"type": "click", "pt": "pen"},
        {"type": "tile-hold", "ms": 250, "moved": 2, "target": "tile:3"},
    ]
    with pytest.raises(ValueError):
        log.add([{"type": "click"}] * (MAX_BATCH + 1))
    with pytest.raises(ValueError):
        log.add({"type": "click"})


def test_the_log_is_bounded_and_can_be_cleared():
    log = TouchLog(FakeClock())
    log.start()
    for _ in range(MAX_EVENTS // MAX_BATCH + 2):
        log.add([{"type": "pointermove"}] * MAX_BATCH)
    assert len(log.events()) == MAX_EVENTS
    log.clear()
    assert log.events() == []
    log.stop()
    assert not log.active()
