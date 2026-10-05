# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.hue.fake import FakeBridge, FakeHue
from muckebox.runtime.clock import FakeClock
from muckebox.runtime.cooldown import CoolingDown
from muckebox.runtime.lanes import InlineLane
from muckebox.runtime.lights import Lights, LightsUnavailable
from muckebox.runtime.timers import SleepTimer, TimersFile
from muckebox.settings import HueBridge

SLOTS = [
    {"scene": "scene-bright", "picture": "sun"},
    {"scene": "scene-night", "picture": "moon"},
]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def hue():
    return FakeHue(FakeBridge())


@pytest.fixture
def make_lights(tmp_path, make_store, clock, hue):
    def make(paired=True, slots=SLOTS):
        store = make_store()
        if paired:
            bridge = hue.bridge
            store.set_hue_bridge(
                HueBridge(bridge.ip, bridge.id, bridge.name, bridge.key, bridge.fingerprint)
            )
            store.set_hue({"room": "room-kids", "slots": slots})
        timers = TimersFile(tmp_path / "timers.json")
        lights = Lights(store, timers, clock, hue, lane_factory=lambda *a: InlineLane())
        lights.poll()
        return lights

    return make


def active(lights):
    return [slot["slot"] for slot in lights.document()["slots"] if slot["active"]]


def test_a_tap_switches_the_scene_on_and_a_second_tap_the_room_off(make_lights, hue, clock):
    lights = make_lights()
    doc = lights.document()
    assert doc["available"] is True
    assert [(s["slot"], s["picture"]) for s in doc["slots"]] == [(1, "sun"), (2, "moon")]
    assert active(lights) == []
    lights.toggle(2)
    assert active(lights) == [2]
    assert ("recall", "scene-night") in hue.bridge.calls
    clock.advance(3)
    lights.toggle(1)  # another scene: switches over
    assert active(lights) == [1]
    clock.advance(3)
    lights.toggle(1)  # the active one again: the room goes dark
    assert active(lights) == []
    assert ("off", "group-kids") in hue.bridge.calls
    assert "group-kids" not in hue.bridge.lit


def test_light_taps_wait_three_seconds(make_lights, clock):
    lights = make_lights()
    lights.toggle(1)
    with pytest.raises(CoolingDown) as info:
        lights.toggle(2)
    assert info.value.retry_in == 3
    clock.advance(3)
    lights.toggle(2)


def test_changes_from_the_hue_app_show_up(make_lights, hue):
    lights = make_lights()
    hue.bridge.recall("scene-night")  # someone used the app or a wall switch
    lights.poll()
    assert active(lights) == [2]


def test_an_unreachable_bridge_dims_the_buttons_and_fails_fast(make_lights, hue, clock):
    lights = make_lights()
    hue.bridge.reachable = False
    lights.poll()
    assert lights.document()["available"] is False
    assert lights.status()["problem"] == "hue_unreachable"
    with pytest.raises(LightsUnavailable) as info:
        lights.toggle(1)
    assert info.value.code == "hue_unreachable"
    hue.bridge.reachable = True
    clock.advance(60)
    lights.poll()
    assert lights.document()["available"] is True


def test_a_scene_deleted_in_the_hue_app_is_left_out(make_lights, hue):
    lights = make_lights()
    hue.bridge.scenes_list = [s for s in hue.bridge.scenes_list if s.id != "scene-bright"]
    lights.poll()
    assert [s["slot"] for s in lights.document()["slots"]] == [2]
    with pytest.raises(LightsUnavailable) as info:
        lights.toggle(1)
    assert info.value.code == "hue_not_found"
    assert lights.document()["available"] is True  # the bridge itself works


def test_without_a_bridge_there_are_no_buttons(make_lights):
    lights = make_lights(paired=False)
    assert lights.document() == {"available": False, "slots": [], "cooldown": 3}
    with pytest.raises(LightsUnavailable) as info:
        lights.toggle(1)
    assert info.value.code == "hue_not_configured"
    with pytest.raises(LookupError):
        make_lights().toggle(4)


def test_a_changed_certificate_is_refused_and_reconnect_takes_it_over(make_lights, hue):
    lights = make_lights()
    hue.bridge.fingerprint = "cd" * 32  # e.g. after a firmware update
    lights.poll()
    assert lights.status()["problem"] == "hue_certificate_changed"
    lights.reconnect()
    assert lights.store.current().hue.bridge.fingerprint == "cd" * 32
    assert lights.store.current().hue.room == "room-kids"  # the choice stays
    assert lights.document()["available"] is True


def test_reconnect_needs_the_key_to_work(make_lights, hue):
    lights = make_lights()
    hue.bridge.key = "another-key"  # Muckebox was removed in the Hue app
    with pytest.raises(LightsUnavailable) as info:
        lights.reconnect()
    assert info.value.code == "hue_unauthorized"


def test_pairing_waits_for_the_button(make_lights, hue):
    lights = make_lights(paired=False)
    hue.bridge.button_pressed = False
    with pytest.raises(LightsUnavailable) as info:
        lights.pair(hue.bridge.ip)
    assert info.value.code == "hue_link_button"
    assert lights.store.current().hue.bridge is None  # nothing written
    hue.bridge.button_pressed = True
    bridge = lights.pair(hue.bridge.ip)
    assert lights.store.current().hue.bridge == bridge
    assert [b.ip for b in lights.search(None)] == [hue.bridge.ip]


def test_the_parents_see_rooms_with_lights_and_their_scenes(make_lights, hue):
    view = make_lights().admin_view()
    assert {r["id"] for r in view["rooms"]} == {"room-kids", "room-living"}
    assert {"id": "scene-night", "name": "Nachtlicht", "room": "room-kids"} in view["scenes"]
    hue.bridge.reachable = False
    assert make_lights().admin_view()["problem"] == "hue_unreachable"


# -- the sleep timer's end ----------------------------------------------------------------------


def end_sleep_timer(lights, clock, lights_setting, minutes_ago=0.1):
    store = lights.store
    store.set_sleep_timer({"enabled": True, "minutes": 30, "lights": lights_setting})
    now = clock.time()
    with lights.timers.change() as state:
        state.sleep = SleepTimer(ends_at=now - minutes_ago * 60, lock_end=now + 36000, fade=60)


@pytest.mark.parametrize(
    ("setting", "lit", "night"), [("off", False, False), ("scene-night", True, True)]
)
def test_the_sleep_timer_ends_with_lights_off_or_a_scene(
    make_lights, hue, clock, setting, lit, night
):
    lights = make_lights()
    lights.toggle(1)  # the bright scene is on
    end_sleep_timer(lights, clock, setting)
    lights.poll()
    assert ("group-kids" in hue.bridge.lit) is lit
    assert (2 in active(lights)) is night
    calls = len(hue.bridge.calls)
    lights.poll()  # once per end
    assert not [c for c in hue.bridge.calls[calls:] if c[0] in ("recall", "off")]


def test_the_sleep_timer_keeps_the_lights_by_default(make_lights, hue, clock):
    lights = make_lights()
    lights.toggle(1)
    end_sleep_timer(lights, clock, "keep")
    lights.poll()
    assert active(lights) == [1]


def test_after_a_restart_the_end_is_handled_once_never_hours_later(
    make_lights, hue, clock, tmp_path
):
    lights = make_lights()
    end_sleep_timer(lights, clock, "off", minutes_ago=5)
    lights.toggle(1)
    lights.timers.save()
    # Muckebox restarts five minutes after the end: still switched once.
    again = Lights(
        lights.store, TimersFile(tmp_path / "timers.json"), clock, hue,
        lane_factory=lambda *a: InlineLane(),
    )  # fmt: skip
    again.poll()
    assert active(again) == []
    late = make_lights()
    late.toggle(1)
    end_sleep_timer(late, clock, "off", minutes_ago=20)
    late.poll()
    assert active(late) == [1]  # too late: left alone


def test_the_sleep_end_waits_for_an_unreachable_bridge(make_lights, hue, clock):
    lights = make_lights()
    lights.toggle(1)
    end_sleep_timer(lights, clock, "off")
    hue.bridge.reachable = False
    lights.poll()
    hue.bridge.reachable = True
    clock.advance(60)
    lights.poll()
    assert "group-kids" not in hue.bridge.lit
