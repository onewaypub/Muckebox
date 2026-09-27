# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import threading
import time

import pytest

from muckebox.config import load_settings
from muckebox.library import Library, favorite_source, sharelink_source
from muckebox.runtime.breaker import CircuitBreaker
from muckebox.runtime.clock import FakeClock
from muckebox.runtime.lanes import InlineLane, Lane
from muckebox.runtime.service import Busy, Runtime, Unavailable
from muckebox.runtime.volume_guard import VolumeGuard
from muckebox.sonos.errors import ServiceUnavailable, SonosUnreachable
from muckebox.sonos.fake import FakeSonos
from muckebox.sonos.model import Route, ShareLinkRef


def settings(tmp_path, **env):
    return load_settings(
        {"SONOS_IP": "192.0.2.10", "DATA_DIR": str(tmp_path), "MAX_VOLUME": "25", **env}
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def fake():
    return FakeSonos()


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "library.json")


def add_favorite(library, fake, index):
    favorite = fake.favorites[index]
    return library.add(
        favorite.title,
        favorite_source(favorite.item_id, favorite.ref, favorite.route, favorite.description),
    )


def make_runtime(tmp_path, fake, library, clock, **env):
    return Runtime(
        settings(tmp_path, **env),
        fake,
        library,
        clock=clock,
        lane_factory=lambda name, idle, interval: InlineLane(name),
    )


@pytest.fixture
def runtime(tmp_path, fake, library, clock):
    rt = make_runtime(tmp_path, fake, library, clock)
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


def test_fight_is_detected_and_enforcement_continues(fake, clock):
    guard = VolumeGuard(fake, lambda: 25, clock)
    for _ in range(11):
        fake.volume = 90
        guard.step()
        clock.advance(0.5)
    assert guard.fighting
    assert fake.volume == 25
    clock.advance(20)
    fake.volume = 90
    guard.step()
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
    assert doc["volume"] == {"value": None, "max": 25, "step": 3}
    assert doc["api"] == 1


def test_reading_state_never_calls_the_speaker(runtime, fake):
    fake.calls.clear()
    for _ in range(5):
        runtime.state_document()
    assert fake.calls == []


def test_config_error_disables_control(tmp_path, fake, library, clock):
    rt = make_runtime(tmp_path, fake, library, clock, SONOS_IP="", SONOS_ROOM="")
    assert rt.state_document()["sonos"]["status"] == "config_error"
    rt.start()  # does not start the lanes
    tile = add_favorite(library, fake, 0)
    with pytest.raises(Unavailable) as info:
        rt.play_tile(tile.id)
    assert info.value.code == "config_error"


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


def test_highlight_survives_a_restart(tmp_path, fake, library, clock, runtime):
    tile = add_favorite(library, fake, 0)
    runtime.play_tile(tile.id)
    restarted = make_runtime(tmp_path, fake, library, clock)
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


def test_fixed_volume_is_detected_on_first_contact(tmp_path, library, clock):
    fake = FakeSonos(fixed=True)
    rt = make_runtime(tmp_path, fake, library, clock)
    rt.poll_transport()
    assert rt.status()["volume_guard"]["fixed_volume"] is True


# -- runtime with real threads ---------------------------------------------------------


def threaded_runtime(tmp_path, fake, library):
    return Runtime(settings(tmp_path), fake, library)


def test_rapid_taps_start_only_once(tmp_path, library):
    release = threading.Event()
    fake = FakeSonos()
    fake.on_call = lambda name: name == "play_favorite" and release.wait(5)
    tile = add_favorite(library, fake, 0)
    other = add_favorite(library, fake, 1)
    rt = threaded_runtime(tmp_path, fake, library)
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


def test_guard_works_while_a_start_is_stuck(tmp_path, library):
    release = threading.Event()
    fake = FakeSonos()
    fake.on_call = lambda name: name == "play_favorite" and release.wait(10)
    tile = add_favorite(library, fake, 0)
    rt = threaded_runtime(tmp_path, fake, library)
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
