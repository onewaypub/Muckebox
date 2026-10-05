# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import threading
import time

import pytest

from muckebox.library import Library, favorite_source, sharelink_source
from muckebox.localtime import ZoneResolver
from muckebox.runtime.breaker import CircuitBreaker
from muckebox.runtime.clock import FakeClock
from muckebox.runtime.cooldown import CoolingDown
from muckebox.runtime.lanes import InlineLane, Lane
from muckebox.runtime.service import Busy, Runtime, Unavailable
from muckebox.runtime.volume_guard import VolumeGuard
from muckebox.settings import SettingsError
from muckebox.sonos.errors import ServiceUnavailable, SonosUnreachable
from muckebox.sonos.model import Route, ShareLinkRef


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def fake(fake_sonos):
    return fake_sonos


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "library.json")


def add_favorite(library, fake, index):
    favorite = fake.favorites[index]
    return library.add(
        favorite.title,
        favorite_source(favorite.item_id, favorite.ref, favorite.route, favorite.description),
    )


@pytest.fixture
def make_runtime(tmp_path, household, library, clock, make_store):
    """A runtime for ``room`` (None: not set up yet); inline lanes unless ``threaded``."""

    def make(room="Kinderzimmer", *, threaded=False):
        options = {"zones": ZoneResolver({"TZ": "Europe/Berlin"})}
        if not threaded:
            options |= {
                "clock": clock,
                "lane_factory": lambda name, idle, interval: InlineLane(name),
            }
        return Runtime(
            make_store(room),
            library,
            tmp_path,
            backend_factory=household.backend,
            room_finder=household.find_rooms,
            **options,
        )

    return make


@pytest.fixture
def runtime(make_runtime):
    rt = make_runtime()
    rt.poll_transport()
    return rt


# -- circuit breaker ------------------------------------------------------------


def test_breaker_cooldown_doubles_and_caps(clock):
    breaker = CircuitBreaker(clock, min_cooldown=2, max_cooldown=5)
    assert breaker.allow()
    breaker.failure()
    assert not breaker.allow()
    assert breaker.retry_in() == 2
    clock.advance(2)
    assert breaker.allow()
    breaker.failure()
    assert breaker.retry_in() == 4
    clock.advance(4)
    breaker.failure()
    assert breaker.retry_in() == 5
    breaker.success()
    assert breaker.allow()
    assert breaker.retry_in() is None
    assert not breaker.failing


# -- volume guard -----------------------------------------------------------------


def test_guard_sets_volume_back_to_the_limit(fake, clock):
    guard = VolumeGuard(fake, lambda: 25, clock)
    fake.volume = 80
    assert guard.step() == 25
    assert fake.volume == 25
    assert guard.corrections == 1
    fake.calls.clear()
    assert guard.step() == 25
    assert [c[0] for c in fake.calls] == ["get_volume"]  # no set when within the limit


def test_guard_follows_a_lowered_limit(fake, clock):
    limit = {"value": 25}
    guard = VolumeGuard(fake, lambda: limit["value"], clock)
    fake.volume = 20
    guard.step()
    limit["value"] = 15
    assert guard.step() == 15


@pytest.mark.parametrize(
    ("start", "direction", "expected"),
    [
        (22, "up", 25),
        (25, "up", 25),
        (40, "up", 25),
        (2, "down", 0),
        (10, "down", 5),
        (10, "up", 15),
    ],
)
def test_louder_and_quieter_stay_within_limits(fake, clock, start, direction, expected):
    guard = VolumeGuard(fake, lambda: 25, clock)
    fake.volume = start
    assert guard.change(direction, 5) == expected
    assert fake.volume == expected


def test_louder_never_writes_when_nothing_changes(fake, clock):
    guard = VolumeGuard(fake, lambda: 25, clock)
    fake.volume = 25
    guard.change("up", 3)
    assert ("set_volume", 25) not in fake.calls


def test_unreadable_volume_changes_nothing(fake, clock):
    guard = VolumeGuard(fake, lambda: 25, clock)
    fake.fail_next["get_volume"] = SonosUnreachable()
    with pytest.raises(SonosUnreachable):
        guard.change("up", 3)
    assert not [c for c in fake.calls if c[0] == "set_volume"]


def test_fight_is_detected_at_the_real_poll_rate(fake, clock):
    from muckebox.runtime.volume_guard import FIGHT_CORRECTIONS

    guard = VolumeGuard(fake, lambda: 25, clock)
    for _ in range(FIGHT_CORRECTIONS - 1):
        fake.volume = 90
        guard.step()
        clock.advance(1.05)  # the guard polls a little slower than once per second
    assert not guard.fighting
    fake.volume = 90
    guard.step()
    assert guard.fighting
    assert fake.volume == 25  # enforcement continues
    clock.advance(20)
    guard.step()  # volume is fine again: the fight is over
    assert not guard.fighting


# -- lanes ------------------------------------------------------------------------


def test_lane_runs_jobs_in_order_and_survives_errors():
    ran = []
    idle = threading.Event()
    lane = Lane("test", idle.set, interval=0.05)
    lane.start()
    try:
        first = lane.submit(lambda: ran.append(1) or 1)
        failing = lane.submit(lambda: 1 / 0)
        last = lane.submit(lambda: ran.append(3) or 3)
        assert last.result(2) == 3
        assert first.result() == 1
        with pytest.raises(ZeroDivisionError):
            failing.result()
        assert ran == [1, 3]
        assert idle.wait(2)
    finally:
        lane.stop()


def test_lane_idle_task_errors_do_not_stop_the_lane():
    calls = []

    def idle():
        calls.append(1)
        raise RuntimeError("poll failed")

    lane = Lane("test", idle, interval=0.01)
    lane.start()
    try:
        assert lane.submit(lambda: "still alive").result(2) == "still alive"
        deadline = time.monotonic() + 2
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(calls) >= 3
    finally:
        lane.stop()


# -- runtime: state -------------------------------------------------------------------


def test_first_poll_resolves_the_room(runtime):
    doc = runtime.state_document()
    assert doc["sonos"]["status"] == "ok"
    assert doc["sonos"]["room"] == "Kinderzimmer"
    assert doc["volume"] == {"value": None, "max": 25, "limit": 25, "step": 3}
    assert doc["api"] == 1


def test_reading_state_never_calls_the_speaker(runtime, fake):
    fake.calls.clear()
    for _ in range(5):
        runtime.state_document()
    assert fake.calls == []


def test_without_a_room_nothing_is_controlled(make_runtime, household, library, fake):
    rt = make_runtime(room=None)
    assert rt.state_document()["sonos"] == {"status": "not_configured", "room": None}
    assert not rt.configured
    rt.start()
    rt.poll_transport()
    rt.poll_volume()
    assert fake.calls == []
    tile = add_favorite(library, fake, 0)
    for call in (
        lambda: rt.play_tile(tile.id),
        lambda: rt.transport("toggle"),
        lambda: rt.change_volume("up"),
        rt.favorites,
        lambda: rt.fetch_art("fake-art:1"),
    ):
        with pytest.raises(Unavailable) as info:
            call()
        assert info.value.code == "not_configured"
    assert rt.cached_favorites() is None
    status = rt.status()
    assert status["config_problems"] == ["not_configured", "pin_generated"]
    assert status["breaker"] == {"transport_retry_in": None, "volume_retry_in": None}


# -- runtime: playing tiles -------------------------------------------------------------


def test_play_queue_tile_highlights_it(runtime, fake, library):
    tile = add_favorite(library, fake, 0)  # playlist -> queue
    assert runtime.play_tile(tile.id) == "accepted"
    assert ("play_favorite", tile.source["uri"], Route.QUEUE) in fake.calls
    doc = runtime.state_document()
    assert doc["playback"]["state"] == "playing"
    assert doc["playback"]["tile_id"] == tile.id
    assert doc["pending"] is None
    assert doc["last_error"] is None


def test_play_direct_tile_highlights_it(runtime, fake, library):
    tile = add_favorite(library, fake, 2)  # radio -> direct
    runtime.play_tile(tile.id)
    assert ("play_favorite", tile.source["uri"], Route.DIRECT) in fake.calls
    assert runtime.state_document()["playback"]["tile_id"] == tile.id


def test_share_link_tile(runtime, fake, library):
    link = ShareLinkRef("apple_music", "album", "1440857781")
    tile = library.add("Album", sharelink_source(link, "https://music.apple.com/x"))
    runtime.play_tile(tile.id)
    assert ("play_share_link", link, "Album") in fake.calls
    assert runtime.state_document()["playback"]["tile_id"] == tile.id


def test_tapping_the_playing_tile_does_nothing(runtime, fake, library):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    fake.calls.clear()
    assert runtime.play_tile(tile.id) == "noop"
    assert fake.calls == []


def test_tapping_the_paused_tile_resumes(runtime, fake, library):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    runtime.transport("pause")
    assert runtime.state_document()["playback"]["state"] == "paused"
    assert runtime.play_tile(tile.id) == "resumed"
    assert ("transport", "play") in fake.calls
    assert not [c for c in fake.calls if c[0] == "play_favorite"][1:]
    assert runtime.state_document()["playback"]["state"] == "playing"


def test_other_content_clears_the_highlight(runtime, fake, library):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    fake.media_uri, fake.queue = "x-sonosapi-stream:other", []  # started from the Sonos app
    runtime.poll_transport()
    assert runtime.state_document()["playback"]["tile_id"] is None
    fake.media_uri, fake.queue = "x-rincon-queue:RINCON_000000000000001400#0", [tile.source["uri"]]
    runtime.poll_transport()
    assert runtime.state_document()["playback"]["tile_id"] is None  # stays forgotten


def test_highlight_survives_a_restart(fake, library, runtime, make_runtime):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    restarted = make_runtime()
    restarted.poll_transport()
    assert restarted.state_document()["playback"]["tile_id"] == tile.id


def test_failed_start_reports_the_error_briefly(runtime, fake, library, clock):
    tile = add_favorite(library, fake, 0)
    fake.fail_next["play_favorite"] = ServiceUnavailable(upnp_code=800)
    assert runtime.play_tile(tile.id) == "accepted"
    doc = runtime.state_document()
    assert doc["last_error"]["code"] == "service_unavailable"
    assert doc["last_error"]["tile_id"] == tile.id
    assert doc["pending"] is None
    clock.advance(61)
    assert runtime.state_document()["last_error"] is None


def test_unknown_tile(runtime):
    from muckebox.library import TileNotFound

    with pytest.raises(TileNotFound):
        runtime.play_tile("t-missing")


def test_unreachable_speaker_fails_fast_and_recovers(runtime, fake, library, clock):
    tile = add_favorite(library, fake, 0)
    fake.reachable = False
    runtime.poll_transport()
    doc = runtime.state_document()
    assert doc["sonos"]["status"] == "sonos_unreachable"
    assert doc["sonos"]["retry_in"] == 2
    fake.calls.clear()
    with pytest.raises(Unavailable) as info:
        runtime.play_tile(tile.id)
    assert info.value.code == "sonos_unreachable"
    assert fake.calls == []  # failed fast, no network call
    fake.reachable = True
    clock.advance(2)
    runtime.poll_transport()
    assert runtime.state_document()["sonos"]["status"] == "ok"
    assert runtime.play_tile(tile.id) == "accepted"


def test_transport_toggle_and_unavailable_actions(runtime, fake, library):
    tile = add_favorite(library, fake, 2)  # radio: no next/previous
    runtime.play_tile(tile.id)
    assert runtime.transport("toggle")["state"] == "paused"
    assert runtime.transport("toggle")["state"] == "playing"
    runtime.transport("next")  # 701 is not an error for the kids
    doc = runtime.state_document()["playback"]
    assert (doc["can_next"], doc["can_prev"]) == (False, False)


def test_invalid_transport_action(runtime):
    with pytest.raises(ValueError):
        runtime.transport("eject")


def test_volume_buttons_and_guard(runtime, fake):
    fake.volume = 24
    assert runtime.change_volume("up")["value"] == 25
    assert runtime.change_volume("up")["value"] == 25
    fake.volume = 80  # somebody used the Sonos app
    runtime.poll_volume()
    assert fake.volume == 25
    assert runtime.state_document()["volume"]["value"] == 25
    assert runtime.status()["volume_guard"]["corrections"] == 1


def test_volume_failure_is_reported(runtime, fake):
    fake.fail_next["get_volume"] = SonosUnreachable()
    with pytest.raises(Unavailable) as info:
        runtime.change_volume("up")
    assert info.value.code == "volume_unknown"


def test_favorites_are_cached(runtime, fake, clock):
    first = runtime.favorites()
    assert len(first) == len(fake.favorites)
    fake.calls.clear()
    runtime.favorites()
    assert fake.calls == []
    runtime.favorites(refresh=True)
    assert ("list_favorites",) in fake.calls
    fake.calls.clear()
    clock.advance(61)
    runtime.favorites()
    assert ("list_favorites",) in fake.calls


def test_fixed_volume_is_detected_on_first_contact(fake, make_runtime):
    fake.fixed = True
    rt = make_runtime()
    rt.poll_transport()
    assert rt.status()["volume_guard"]["fixed_volume"] is True


# -- runtime with real threads ---------------------------------------------------------


def test_rapid_taps_start_only_once(fake, library, make_runtime):
    release = threading.Event()
    fake.on_call = lambda name: name == "play_favorite" and release.wait(5)
    tile = add_favorite(library, fake, 0)
    other = add_favorite(library, fake, 1)
    rt = make_runtime(threaded=True)
    rt.start()
    try:
        assert rt.play_tile(tile.id) == "accepted"
        for _ in range(9):
            with pytest.raises(Busy):
                rt.play_tile(tile.id)
        with pytest.raises(Busy):
            rt.play_tile(other.id)
        assert rt.state_document()["pending"]["tile_id"] == tile.id
        release.set()
        deadline = time.monotonic() + 5
        while rt.state_document()["pending"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len([c for c in fake.calls if c[0] == "play_favorite"]) == 1
        assert rt.state_document()["playback"]["tile_id"] == tile.id
    finally:
        release.set()
        rt.stop()


def test_guard_works_while_a_start_is_stuck(fake, library, make_runtime):
    release = threading.Event()
    fake.on_call = lambda name: name == "play_favorite" and release.wait(10)
    tile = add_favorite(library, fake, 0)
    rt = make_runtime(threaded=True)
    rt.start()
    try:
        rt.play_tile(tile.id)
        fake.volume = 90
        deadline = time.monotonic() + 3
        while fake.volume != 25 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert fake.volume == 25
        assert rt.state_document()["pending"] is not None  # start still stuck
    finally:
        release.set()
        rt.stop()


# -- review regressions ---------------------------------------------------------------


def test_start_counts_even_if_reading_the_state_fails(runtime, fake, library):
    from muckebox.sonos.errors import SonosTimeout

    tile = add_favorite(library, fake, 0)
    fake.fail_next["playback"] = SonosTimeout()
    assert runtime.play_tile(tile.id) == "accepted"
    assert runtime.state_document()["last_error"] is None
    runtime.poll_transport()
    assert runtime.state_document()["playback"]["tile_id"] == tile.id
    fake.calls.clear()
    assert runtime.play_tile(tile.id) == "noop"  # no restart of the album
    assert fake.calls == []


def test_unexpected_errors_are_logged_and_shown(runtime, fake, library, caplog):
    tile = add_favorite(library, fake, 0)
    fake.fail_next["play_favorite"] = KeyError("bug")  # not a SonosError
    runtime.play_tile(tile.id)
    doc = runtime.state_document()
    assert doc["last_error"]["code"] == "sonos_error"
    assert doc["pending"] is None
    assert "failed unexpectedly" in caplog.text


def test_double_tap_on_pause_does_not_resume(runtime, fake, library):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    runtime.transport("pause")
    fake.calls.clear()
    assert runtime.transport("pause")["state"] == "paused"
    assert fake.calls == []  # nothing sent: already paused
    runtime.transport("play")
    assert runtime.transport("play")["state"] == "playing"


def test_limit_shown_to_the_kids_follows_max_volume(runtime):
    runtime.store.set_volume(12, 3)
    assert runtime.state_document()["volume"]["max"] == 12
    assert runtime.status()["volume_guard"]["max"] == 12


def test_unreachable_speaker_makes_the_volume_unknown(runtime, fake):
    fake.volume = 10
    runtime.poll_volume()
    assert runtime.state_document()["volume"]["value"] == 10
    fake.fail_next["get_volume"] = SonosUnreachable()
    runtime.poll_volume()
    assert runtime.state_document()["volume"]["value"] is None


def test_volume_taps_do_not_pile_up(fake, make_runtime):
    release = threading.Event()
    fake.volume = 4
    fake.on_call = lambda name: name == "get_volume" and release.wait(5)
    rt = make_runtime(threaded=True)
    rt.volume_lane.start()
    try:
        results = []

        def tap():
            try:
                rt.change_volume("up")
                results.append("ok")
            except Busy:
                results.append("busy")
            except Unavailable:
                results.append("slow")

        first = threading.Thread(target=tap)
        first.start()
        deadline = time.monotonic() + 2
        while not rt.volume_lane.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        for _ in range(3):
            tap()
        release.set()
        first.join(5)
        assert results.count("busy") == 3
        deadline = time.monotonic() + 2
        while rt.volume_lane.busy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fake.volume == 7  # exactly one step, not four
    finally:
        release.set()
        rt.volume_lane.stop()


def test_stop_waits_at_most_the_timeout_for_busy_lanes(fake, make_runtime):
    """Both lanes stuck on the network: the deadline is shared, not per lane."""
    release = threading.Event()
    busy = {"resolve": threading.Event(), "get_volume": threading.Event()}

    def on_call(name):
        if name in busy:
            busy[name].set()
            release.wait(10)

    fake.on_call = on_call
    rt = make_runtime(threaded=True)
    rt.start()
    try:
        assert all(event.wait(2) for event in busy.values())
        started = time.monotonic()
        rt.stop(timeout=0.5)
        assert time.monotonic() - started < 0.9
    finally:
        release.set()


# -- choosing the room -------------------------------------------------------------------


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_room_search_is_cached_briefly(make_runtime, household, clock):
    rt = make_runtime(room=None)
    rooms = rt.search_rooms(None)
    assert [r.name for r in rooms] == ["Kinderzimmer", "Wohnzimmer"]
    assert rt.search_rooms(None) == rooms
    assert rt.search_rooms(None, refresh=True) == rooms  # "search again" right away: cached
    assert household.searches == [None]
    clock.advance(5)
    rt.search_rooms(None, refresh=True)
    assert household.searches == [None, None]
    clock.advance(15)
    rt.search_rooms(None)
    rt.search_rooms("192.0.2.10")  # another seed is another search
    assert household.searches == [None, None, None, "192.0.2.10"]


def test_room_search_failure(make_runtime, household):
    household.reachable = False
    with pytest.raises(Unavailable) as info:
        make_runtime(room=None).search_rooms(None)
    assert info.value.code == "sonos_unreachable"


def test_choosing_a_room_tests_saves_and_starts_control(make_runtime, household, library):
    rt = make_runtime(room=None)
    rt.search_rooms(None)
    info = rt.choose_room(" Wohnzimmer ", " 192.0.2.11 ")
    assert info.name == "Wohnzimmer"
    stored = rt.store.current()
    assert (stored.room, stored.room_uid, stored.seed_ip) == (
        "Wohnzimmer",
        household.uid("Wohnzimmer"),
        "192.0.2.11",
    )
    assert rt.state_document()["sonos"] == {"status": "ok", "room": "Wohnzimmer", "grouped": False}
    living = household.speaker("Wohnzimmer")
    tile = add_favorite(library, living, 0)
    assert rt.play_tile(tile.id) == "accepted"
    assert [c[0] for c in living.calls].count("play_favorite") == 1
    assert not household.speaker("Kinderzimmer").calls


@pytest.mark.parametrize(("room", "seed_ip"), [("", None), ("x" * 101, None), ("A", "no url")])
def test_invalid_room_choice_changes_nothing(make_runtime, household, room, seed_ip):
    rt = make_runtime(room=None)
    with pytest.raises(SettingsError):
        rt.choose_room(room, seed_ip)
    assert not rt.store.current().configured
    assert not household.speakers


def test_unknown_or_unreachable_room_is_not_saved(runtime, household):
    with pytest.raises(Unavailable) as info:
        runtime.choose_room("Keller", None)
    assert info.value.code == "room_not_found"
    household.speaker("Wohnzimmer").reachable = False
    with pytest.raises(Unavailable) as info:
        runtime.choose_room("Wohnzimmer", None)
    assert info.value.code == "sonos_unreachable"
    assert runtime.store.current().room == "Kinderzimmer"
    assert runtime.state_document()["sonos"]["room"] == "Kinderzimmer"


def test_another_room_starts_fresh(runtime, fake, household, library, clock):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    fake.reachable = False
    runtime.poll_transport()  # the old room's breaker opens
    runtime.choose_room("Wohnzimmer", None)
    doc = runtime.state_document()
    assert doc["sonos"]["status"] == "ok"
    assert doc["playback"]["tile_id"] is None
    assert not (runtime.data_dir / "state.json").exists()
    assert runtime.play_tile(tile.id) == "accepted"  # no breaker carried over


def test_same_room_with_a_new_seed_keeps_the_highlight(runtime, fake, library):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    runtime.choose_room("Kinderzimmer", "192.0.2.10")
    runtime.poll_transport()
    assert runtime.state_document()["playback"]["tile_id"] == tile.id


def test_highlight_file_belongs_to_its_room(runtime, fake, library, make_runtime, household):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    data = json.loads((runtime.data_dir / "state.json").read_text())
    assert data["room_uid"] == household.uid("Kinderzimmer")
    assert data["now_playing"]["tile_id"] == tile.id
    # Another Muckebox setup with the same data folder but another room.
    other = make_runtime(room="Wohnzimmer")
    other.poll_transport()
    assert other.state_document()["playback"]["tile_id"] is None


def test_the_limit_applies_to_the_new_room_at_once(runtime, household):
    living = household.speaker("Wohnzimmer")
    living.volume = 70
    runtime.choose_room("Wohnzimmer", None)
    runtime.poll_volume()
    assert living.volume == 25


def test_new_limit_and_step_apply_at_once(runtime, fake):
    fake.volume = 20
    runtime.store.set_volume(15, 5)
    runtime.poll_volume()
    assert fake.volume == 15
    assert runtime.change_volume("down")["value"] == 10


def test_renamed_room_is_remembered(runtime, fake, household, clock):
    household.rooms[0] = fake.room_name = "Kinderzimmer oben"  # renamed in the Sonos app
    clock.advance(61)
    runtime.poll_transport()
    assert runtime.store.current().room == "Kinderzimmer oben"
    assert runtime.state_document()["sonos"]["room"] == "Kinderzimmer oben"


def test_rename_that_cannot_be_saved_is_only_logged(runtime, fake, household, clock, caplog):
    household.rooms[0] = fake.room_name = "x" * 101  # longer than a stored room name may be
    clock.advance(61)
    runtime.poll_transport()
    assert runtime.state_document()["sonos"]["status"] == "ok"
    assert "Could not save the new room name" in caplog.text


def test_fixed_volume_of_a_chosen_room_is_reported(runtime, household):
    household.speaker("Wohnzimmer").fixed = True
    runtime.choose_room("Wohnzimmer", None)
    assert runtime.status()["volume_guard"]["fixed_volume"] is True


def test_room_switch_while_a_start_is_stuck(fake, household, library, make_runtime):
    """The parents switch rooms while the old room hangs in a start."""
    release, starting = threading.Event(), threading.Event()

    def on_call(name):
        if name == "play_favorite":
            starting.set()
            release.wait(10)

    fake.on_call = on_call
    living = household.speaker("Wohnzimmer")
    living.volume = 20  # below the limit: nothing may raise or lower it
    tile = add_favorite(library, fake, 0)
    rt = make_runtime(threaded=True)
    rt.start()
    try:
        assert rt.play_tile(tile.id) == "accepted"
        assert starting.wait(5)
        rt.choose_room("Wohnzimmer", None)  # runs on the setup lane: not blocked
        assert rt.state_document()["sonos"]["room"] == "Wohnzimmer"
        release.set()
        wait_until(lambda: rt.state_document()["pending"] is None)
        wait_until(lambda: any(c[0] == "get_volume" for c in living.calls))
        doc = rt.state_document()
        assert doc["sonos"] == {"status": "ok", "room": "Wohnzimmer", "grouped": False}
        assert doc["playback"]["tile_id"] is None  # the old room's start is not shown
        assert not (rt.data_dir / "state.json").exists()
        assert living.volume == 20
        assert not [c for c in living.calls if c[0] == "set_volume"]
    finally:
        release.set()
        rt.stop()


def test_only_one_room_search_at_a_time(household, make_runtime):
    release = threading.Event()
    household.on_search = lambda seed: release.wait(10)
    rt = make_runtime(room=None, threaded=True)
    rt.start()
    try:
        first = threading.Thread(target=rt.search_rooms, args=(None,))
        first.start()
        wait_until(lambda: rt.setup_lane.busy)
        with pytest.raises(Busy):
            rt.search_rooms("192.0.2.10")
        with pytest.raises(Busy):
            rt.choose_room("Kinderzimmer", None)
        release.set()
        first.join(5)
        assert household.searches == [None]
    finally:
        release.set()
        rt.stop()


@pytest.mark.parametrize("content", ["null", "[]", "5", '"x"', '{"room_uid": 1}', "{broken"])
def test_odd_state_file_is_ignored(make_runtime, tmp_path, content):
    (tmp_path / "state.json").write_text(content)
    rt = make_runtime()
    rt.poll_transport()
    assert rt.state_document()["playback"]["tile_id"] is None


def test_a_slow_disk_never_delays_the_volume_guard(fake, library, make_runtime, monkeypatch):
    """Writing state.json can take seconds on a sleeping NAS disk."""
    from muckebox.runtime import service

    release, writing = threading.Event(), threading.Event()

    def slow_write(*args, **kwargs):
        writing.set()
        release.wait(10)

    monkeypatch.setattr(service, "atomic_write", slow_write)
    tile = add_favorite(library, fake, 0)
    rt = make_runtime(threaded=True)
    rt.start()
    try:
        rt.play_tile(tile.id)
        assert writing.wait(5)
        fake.volume = 90
        wait_until(lambda: fake.volume == 25, timeout=3)  # while the write still hangs
        assert rt.change_volume("down")["value"] == 22
    finally:
        release.set()
        rt.stop()


def test_guard_keeps_the_limit_while_settings_json_is_broken(runtime, fake, tmp_path, monkeypatch):
    monkeypatch.setattr("muckebox.settings.RELOAD_INTERVAL", 0)
    (tmp_path / "settings.json").write_text('{"schema": 1, "sonos": null}')
    fake.volume = 90
    runtime.poll_volume()
    assert fake.volume == 25


# -- usage times, override and sleep timer -----------------------------------------------

from datetime import datetime as _datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from muckebox.runtime.timekeeper import Refused  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
DAILY = {
    "enabled": True,
    "fade_minutes": 10,
    "days": {
        day: {"from": "07:00", "to": "19:00"}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    },
}


def at(clock, text):
    """Set the wall clock to a local Berlin time (2026-09-28 is a Monday)."""
    clock.set_time(_datetime.fromisoformat(text).replace(tzinfo=BERLIN).timestamp())


@pytest.fixture
def evening(runtime, fake, library, clock):
    """Kids listening to a Muckebox tile shortly before the end of the usage time."""
    runtime.store.set_schedule(DAILY)
    at(clock, "2026-09-28 18:40")
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    fake.volume = 20
    runtime.poll_volume()
    return tile


def test_the_fade_lowers_the_volume_step_by_step(runtime, fake, clock, evening):
    at(clock, "2026-09-28 18:55")  # halfway through the fade
    runtime.poll_volume()
    assert fake.volume == 12  # 20 x (1 - 0.8 / 2)
    doc = runtime.state_document()
    assert doc["schedule"]["phase"] == "fading"
    assert (doc["volume"]["max"], doc["volume"]["limit"]) == (25, 12)
    assert runtime.status()["volume_guard"]["corrections"] == 0  # fading is no correction
    assert runtime.change_volume("up")["value"] == 12  # "louder" stops at the fade
    at(clock, "2026-09-28 18:59:30")
    runtime.poll_volume()
    assert fake.volume == 5


def test_the_end_pauses_once_and_restores_the_volume(runtime, fake, clock, evening):
    at(clock, "2026-09-28 18:58")
    runtime.poll_volume()
    at(clock, "2026-09-28 19:00:10")
    runtime.poll_transport()
    assert fake.state == "paused"
    runtime.poll_transport()  # confirmed: the volume from before the fade comes back
    assert fake.volume == 20
    doc = runtime.state_document()
    assert doc["schedule"]["phase"] == "closed"
    assert doc["playback"]["can_next"] is False
    fake.state = "playing"  # an adult starts music from the Sonos app
    at(clock, "2026-09-28 19:05")
    runtime.poll_transport()
    assert fake.state == "playing"  # left alone


def test_bedtime_allows_only_pause_and_quieter(runtime, fake, library, clock, evening):
    at(clock, "2026-09-28 19:00:10")
    for command in (
        lambda: runtime.play_tile(evening.id),
        lambda: runtime.change_volume("up"),
        lambda: runtime.transport("next"),
    ):
        with pytest.raises(Refused) as info:
            command()
        assert info.value.code == "bedtime"
    assert runtime.transport("toggle")["state"] == "paused"  # pausing is fine
    with pytest.raises(Refused):
        runtime.transport("toggle")  # playing again is not
    assert runtime.change_volume("down")["value"] == 17


def test_a_restart_just_after_the_end_still_pauses(make_runtime, fake, library, clock, evening):
    at(clock, "2026-09-28 19:05")
    restarted = make_runtime()
    restarted.poll_transport()
    assert fake.state == "paused"


def test_long_after_the_end_nothing_is_paused(make_runtime, fake, clock, evening):
    at(clock, "2026-09-28 19:20")
    restarted = make_runtime()
    restarted.poll_transport()
    assert fake.state == "playing"


def test_at_bedtime_a_grouped_kids_room_leaves_the_group(runtime, fake, clock, evening):
    fake.media_uri, fake.queue = "x-sonosapi-stream:other", []  # the living room's radio
    fake.grouped = True
    at(clock, "2026-09-28 19:00:10")
    runtime.poll_transport()
    assert ("leave_group",) in fake.calls  # the kids room is silent ...
    assert fake.state == "stopped"  # ... the living room plays on (not simulated)
    runtime.poll_transport()
    assert runtime.state_document()["sonos"]["grouped"] is False


def test_a_tile_plays_only_in_the_kids_room(runtime, fake, library):
    fake.grouped = True
    runtime.poll_transport()
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    assert ("leave_group",) in fake.calls  # left the group, then played alone
    assert (fake.grouped, fake.state) == (False, "playing")
    assert runtime.state_document()["sonos"]["grouped"] is False


def test_pause_on_the_tablet_leaves_the_group_instead_of_stopping_it(runtime, fake):
    fake.grouped, fake.media_uri, fake.state = True, "x-sonosapi-stream:other", "playing"
    runtime.poll_transport()
    runtime.transport("pause")
    assert ("leave_group",) in fake.calls
    assert fake.state == "stopped"


def test_transitioning_is_paused_on_the_next_poll(runtime, fake, clock, evening):
    at(clock, "2026-09-28 19:00:10")
    fake.state = "transitioning"  # a pause may not take effect yet
    runtime.poll_transport()
    fake.state = "playing"
    runtime.poll_transport()  # not given up: tried again
    assert fake.state == "paused"


def test_an_override_opens_and_ends_with_a_fade_again(runtime, fake, clock, evening):
    at(clock, "2026-09-28 19:00:10")
    runtime.poll_transport()
    runtime.poll_transport()
    runtime.keeper.override(minutes=15)
    assert runtime.play_tile(evening.id) == "resumed"
    assert runtime.state_document()["schedule"]["phase"] == "open"
    at(clock, "2026-09-28 19:16")
    assert runtime.state_document()["schedule"]["phase"] == "closed"
    runtime.poll_transport()
    assert fake.state == "paused"  # the override's own end


def test_override_needs_a_schedule_or_a_sleep_lock(runtime):
    with pytest.raises(Refused) as info:
        runtime.keeper.override(minutes=15)
    assert info.value.code == "schedule_off"


def test_sleep_timer_fades_pauses_and_locks_until_morning(runtime, fake, library, clock):
    runtime.store.set_sleep_timer({"enabled": True, "minutes": 30, "wake": "07:00"})
    at(clock, "2026-09-28 18:00")
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    fake.volume = 20
    sleep = runtime.keeper.start_sleep_timer()
    assert runtime.state_document()["sleep_timer"]["ends_at"] == int(sleep.ends_at)
    at(clock, "2026-09-28 18:25")  # the last 10 minutes fade
    runtime.poll_volume()
    assert fake.volume < 20
    at(clock, "2026-09-28 18:30:05")
    runtime.poll_transport()
    assert fake.state == "paused"
    with pytest.raises(Refused):
        runtime.play_tile(tile.id)
    at(clock, "2026-09-29 06:59")
    assert runtime.state_document()["schedule"]["phase"] == "closed"
    at(clock, "2026-09-29 07:00:01")
    assert runtime.state_document()["schedule"]["phase"] == "off"


def test_a_parent_ends_the_sleep_lock(runtime, fake, library, clock):
    runtime.store.set_sleep_timer({"enabled": True, "minutes": 5})
    at(clock, "2026-09-28 18:00")
    runtime.keeper.start_sleep_timer()
    at(clock, "2026-09-28 18:10")
    assert runtime.state_document()["schedule"]["phase"] == "closed"
    runtime.keeper.override(minutes=30)
    tile = add_favorite(library, fake, 0)
    assert runtime.play_tile(tile.id) == "accepted"


def test_sleep_timer_rules(runtime, clock):
    with pytest.raises(Refused) as info:
        runtime.keeper.start_sleep_timer()
    assert info.value.code == "sleep_timer_off"
    runtime.store.set_sleep_timer({"enabled": True, "minutes": 30})
    first = runtime.keeper.start_sleep_timer()
    clock.advance(60)
    assert runtime.keeper.start_sleep_timer() == first  # not restarted
    runtime.keeper.cancel_sleep_timer()
    assert runtime.state_document()["sleep_timer"]["ends_at"] is None


def test_timers_survive_a_restart(runtime, make_runtime, clock, evening):
    at(clock, "2026-09-28 19:05")
    runtime.keeper.override(minutes=30)
    runtime.poll_transport()  # saves timers.json
    restarted = make_runtime()
    assert restarted.state_document()["schedule"]["phase"] == "open"


# -- Weiterhören (resume albums) -------------------------------------------------------

from muckebox.sonos.model import StartAt  # noqa: E402

TRACKS = [f"x-sonos-http:track%3a{n}.mp4?sid=204" for n in (1, 2, 3)]


@pytest.fixture
def album(fake, library):
    """The demo audio play (an album) with three tracks."""
    fake.albums[fake.favorites[1].ref.uri] = TRACKS
    return add_favorite(library, fake, 1)


def listen_until(runtime, fake, track, seconds, state="playing"):
    fake.track, fake.seconds, fake.state = track, seconds, state
    runtime.poll_transport()


def test_an_album_continues_where_it_stopped(runtime, fake, library, album, clock):
    runtime.play_tile(album.id)
    assert fake.starts[-1] is None
    listen_until(runtime, fake, 2, 300)
    radio = add_favorite(library, fake, 2)
    runtime.play_tile(radio.id)
    runtime.play_tile(album.id)
    assert fake.starts[-1] == StartAt(track=2, seconds=300, track_uri=TRACKS[1])
    assert (fake.track, fake.seconds) == (2, 295)


def test_playlists_start_from_the_beginning(runtime, fake, library):
    playlist = add_favorite(library, fake, 0)
    fake.albums[fake.favorites[0].ref.uri] = TRACKS
    runtime.play_tile(playlist.id)
    listen_until(runtime, fake, 2, 300)
    runtime.play_tile(add_favorite(library, fake, 2).id)
    runtime.play_tile(playlist.id)
    assert fake.starts[-1] is None


def test_an_album_heard_to_the_end_starts_again(runtime, fake, library, album):
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 3, 1185)
    listen_until(runtime, fake, 1, 0, state="stopped")  # Sonos stops after the last track
    runtime.play_tile(add_favorite(library, fake, 2).id)
    runtime.play_tile(album.id)
    assert fake.starts[-1] is None


def test_positions_are_read_only_every_few_seconds(runtime, fake, album, clock):
    runtime.play_tile(album.id)
    runtime.poll_transport()
    fake.calls.clear()
    runtime.poll_transport()
    assert ("position",) not in fake.calls
    clock.advance(11)
    runtime.poll_transport()
    assert ("position",) in fake.calls


def test_positions_are_saved_on_pause_and_rarely_while_playing(runtime, fake, album, clock):
    path = runtime.data_dir / "resume.json"
    runtime.play_tile(album.id)
    clock.advance(11)
    listen_until(runtime, fake, 2, 60)
    runtime.poll_transport()
    assert not path.exists() or album.id not in path.read_text()  # not yet
    listen_until(runtime, fake, 2, 70, state="paused")
    runtime.poll_transport()  # the next poll writes it
    assert '"track": 2' in path.read_text()


def test_resume_can_be_switched_off_per_tile(runtime, fake, library, album):
    library.set_resume(album.id, False)
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 2, 300)
    runtime.play_tile(add_favorite(library, fake, 2).id)
    runtime.play_tile(album.id)
    assert fake.starts[-1] is None


def test_removed_tiles_lose_their_position(runtime, fake, library, album):
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 2, 300)
    library.remove(album.id)
    runtime.poll_transport()
    assert runtime.resume.saved(album.id) is None


def test_resume_file_survives_a_restart(runtime, make_runtime, fake, library, album):
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 2, 300, state="paused")
    runtime.stop()
    assert make_runtime().resume.get(album.id) == StartAt(2, 300, TRACKS[1])


def test_a_game_mute_left_behind_is_undone_on_start(make_runtime, fake):
    first = make_runtime()
    with first.timers.change() as state:
        state.game_mute = True
    first.timers.save()
    fake.muted = True
    restarted = make_runtime()
    restarted.poll_transport()
    assert fake.muted is False
    assert restarted.timers.state.game_mute is False


def test_more_time_during_the_fade_keeps_the_volume_from_before(runtime, fake, clock, evening):
    at(clock, "2026-09-28 18:55")
    runtime.poll_volume()
    assert fake.volume == 12
    runtime.keeper.override(minutes=15)  # until 19:10: open again until 19:00
    runtime.poll_transport()
    assert fake.volume == 20  # the fade stopped: the volume from before is back
    at(clock, "2026-09-28 19:05")
    runtime.poll_volume()
    assert fake.volume == 12  # the new fade starts from 20 again
    at(clock, "2026-09-28 19:10:05")
    runtime.poll_transport()
    runtime.poll_transport()
    assert (fake.state, fake.volume) == ("paused", 20)


def test_ending_an_override_fades_first(runtime, fake, clock, evening):
    at(clock, "2026-09-28 19:00:10")
    runtime.keeper.override(minutes=60)
    runtime.play_tile(evening.id)
    at(clock, "2026-09-28 19:20")
    runtime.keeper.end_override()
    runtime.poll_transport()
    assert fake.state == "playing"  # not cut off
    at(clock, "2026-09-28 19:25")
    runtime.poll_volume()
    assert fake.volume < 20
    at(clock, "2026-09-28 19:30:05")
    runtime.poll_transport()
    assert fake.state == "paused"


def test_from_the_start_also_for_the_loaded_album(runtime, fake, library, album, clock):
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 2, 300, state="paused")
    runtime.restart_tile(album.id)  # "Von vorn" while it is still loaded
    clock.advance(11)
    runtime.poll_transport()
    assert runtime.resume.saved(album.id) is None  # not recorded again
    assert runtime.play_tile(album.id) == "accepted"  # a fresh start, not "resumed"
    assert fake.starts[-1] is None
    listen_until(runtime, fake, 3, 30)
    clock.advance(11)
    runtime.poll_transport()
    assert runtime.resume.saved(album.id) is not None  # recorded again after the start


def test_ending_an_override_inside_the_window_just_drops_it(runtime, clock, evening):
    at(clock, "2026-09-28 17:00")
    runtime.keeper.override(minutes=30)
    phase = runtime.keeper.end_override()
    assert phase.kind == "open"
    assert runtime.timers.state.override is None


# -- "anti disco": taps of the same kind wait a moment ------------------------------------


def test_after_a_tile_tap_other_tiles_wait(runtime, fake, library, clock):
    first, second = add_favorite(library, fake, 0), add_favorite(library, fake, 2)
    assert runtime.play_tile(first.id, tap=True) == "accepted"
    with pytest.raises(CoolingDown) as info:
        runtime.play_tile(second.id, tap=True)
    assert info.value.retry_in == 5
    assert runtime.state_document()["playback"]["tile_id"] == first.id
    clock.advance(4)
    with pytest.raises(CoolingDown) as info:
        runtime.play_tile(second.id, tap=True)
    assert info.value.retry_in == 1
    clock.advance(1)
    assert runtime.play_tile(second.id, tap=True) == "accepted"
    assert runtime.state_document()["cooldown"] == {"tile": 5, "skip": 3, "toggle": 1}


def test_the_tile_wait_is_a_setting_and_zero_switches_it_off(runtime, fake, library):
    runtime.store.set_controls({"tap_cooldown": 0, "idle_minutes": 60})
    first, second = add_favorite(library, fake, 0), add_favorite(library, fake, 2)
    runtime.play_tile(first.id, tap=True)
    assert runtime.play_tile(second.id, tap=True) == "accepted"


def test_the_paused_tile_resumes_during_the_tile_wait(runtime, fake, library, clock):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id, tap=True)
    clock.advance(1)
    runtime.transport("pause", tap=True)
    clock.advance(1)
    assert runtime.play_tile(tile.id, tap=True) == "resumed"


def test_next_and_previous_wait_three_seconds_play_and_pause_one(runtime, fake, library, clock):
    runtime.play_tile(add_favorite(library, fake, 0).id)
    runtime.transport("next", tap=True)
    with pytest.raises(CoolingDown):
        runtime.transport("previous", tap=True)
    runtime.transport("pause", tap=True)  # another group
    with pytest.raises(CoolingDown):
        runtime.transport("play", tap=True)
    clock.advance(1)
    runtime.transport("play", tap=True)
    clock.advance(2)
    runtime.transport("previous", tap=True)


def test_refused_taps_do_not_start_a_wait(runtime, fake, library, clock):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    runtime.transport("pause", tap=True)
    clock.advance(1)
    runtime.transport("pause", tap=True)  # already the state: nothing sent ...
    runtime.transport("play", tap=True)  # ... and nothing to wait for


def test_internal_starts_never_wait(runtime, fake, library):
    first, second = add_favorite(library, fake, 0), add_favorite(library, fake, 2)
    runtime.play_tile(first.id, tap=True)
    assert runtime.play_tile(second.id) == "accepted"  # e.g. the freeze dance music


# -- no tap for a long time: fade and pause --------------------------------------------


@pytest.fixture
def listening(runtime, fake, library, clock):
    """A tile tapped on the tablet, playing at volume 20; pause after 60 minutes."""
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id, tap=True)
    fake.volume = 20
    runtime.poll_volume()
    runtime.poll_transport()
    return tile


def test_without_a_tap_the_music_fades_and_pauses_once(runtime, fake, clock, listening):
    clock.advance(59 * 60 - 1)
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")
    clock.advance(31)  # halfway through the one-minute fade
    runtime.poll_volume()
    assert fake.volume == 12  # 20 x (1 - 0.8 / 2)
    assert runtime.state_document()["volume"]["limit"] == 12
    assert runtime.status()["volume_guard"]["corrections"] == 0
    clock.advance(30)
    runtime.poll_transport()
    assert fake.state == "paused"
    runtime.poll_transport()  # confirmed: the volume for the next start comes back
    assert fake.volume == 20
    assert runtime.status()["idle"]["paused_at"] is not None
    # No lock: the next tap plays as usual and is not paused again at once.
    assert runtime.state_document()["schedule"]["phase"] == "off"
    assert runtime.transport("play", tap=True)["state"] == "playing"
    clock.advance(30 * 60)
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")


def test_any_tap_starts_the_time_again(runtime, fake, clock, listening):
    clock.advance(50 * 60)
    runtime.change_volume("down")  # a tap on the tablet
    volume = fake.volume
    clock.advance(50 * 60)
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (volume, "playing")


def test_a_tap_during_the_fade_brings_the_volume_back(runtime, fake, clock, listening):
    clock.advance(59 * 60 + 30)
    runtime.poll_volume()
    assert fake.volume == 12
    runtime.transport("next", tap=True)
    runtime.poll_transport()
    assert fake.volume == 20
    assert fake.state == "playing"


def test_only_tiles_are_paused_not_music_from_the_sonos_app(runtime, fake, clock, listening):
    fake.media_uri = "x-sonosapi-stream:something-else"  # an adult plays other music
    runtime.poll_transport()
    assert runtime.state_document()["playback"]["tile_id"] is None
    clock.advance(2 * 60 * 60)
    runtime.poll_transport()
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")


def test_paused_time_does_not_count(runtime, fake, clock, listening):
    runtime.transport("pause")  # e.g. from the Sonos app
    runtime.poll_transport()
    clock.advance(59 * 60)
    runtime.poll_transport()
    fake.state = "playing"
    runtime.poll_transport()
    clock.advance(30 * 60)
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")


def test_zero_minutes_switch_the_pause_off(runtime, fake, clock, listening):
    runtime.store.set_controls({"tap_cooldown": 5, "idle_minutes": 0})
    clock.advance(5 * 60 * 60)
    runtime.poll_volume()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")


def test_other_music_lowered_by_a_late_fade_gets_its_volume_back(runtime, fake, clock, listening):
    fake.media_uri = "x-sonosapi-stream:something-else"
    clock.advance(2 * 60 * 60)  # the guard runs before the transport poll noticed
    runtime.poll_volume()
    runtime.poll_transport()
    runtime.poll_transport()
    assert (fake.volume, fake.state) == (20, "playing")


# -- the "big" kids view: track, progress, layout ------------------------------------------


def test_the_big_view_shows_the_track_and_its_progress(runtime, fake, library, album, clock):
    runtime.play_tile(album.id)
    listen_until(runtime, fake, 2, 300)
    doc = runtime.state_document()
    assert doc["view"] == {
        "profile": "small",
        "skip_buttons": False,
        "now_view": True,
        "tap_sound": True,
    }
    assert doc["track"] is None  # the small view shows no titles
    runtime.store.set_controls({"profile": "big"})
    clock.advance(10)
    runtime.poll_transport()
    doc = runtime.state_document()
    assert doc["view"]["profile"] == "big"
    track = doc["track"]
    assert (track["number"], track["count"], track["title"]) == (2, 3, "Kapitel 2")
    assert (track["seconds"], track["duration"], track["playing"]) == (300, 1200, True)
    assert doc["progress"][album.id] == round((1 + 300 / 1200) / 3, 2)


def test_the_big_view_shows_no_track_for_the_radio(runtime, fake, library):
    runtime.store.set_controls({"profile": "big"})
    runtime.play_tile(add_favorite(library, fake, 2).id)  # radio: no position
    runtime.poll_transport()
    doc = runtime.state_document()
    assert doc["track"] is None
    assert doc["progress"] == {}
