# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Games on the server: may they start, the daily game time, the freeze dance."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muckebox.library import Library, favorite_source
from muckebox.localtime import ZoneResolver
from muckebox.runtime.clock import FakeClock
from muckebox.runtime.games import MUTE_LEASE, UnknownGame
from muckebox.runtime.lanes import InlineLane
from muckebox.runtime.service import Runtime, Unavailable
from muckebox.runtime.timekeeper import Refused
from muckebox.sonos.errors import ServiceUnavailable, SonosUnreachable

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def clock():
    clock = FakeClock()
    clock.set_time(datetime(2026, 9, 28, 16, 0, tzinfo=BERLIN).timestamp())
    return clock


@pytest.fixture
def runtime(tmp_path, household, make_store, clock):
    rt = Runtime(
        make_store("Kinderzimmer"),
        Library(tmp_path / "library.json"),
        tmp_path,
        backend_factory=household.backend,
        room_finder=household.find_rooms,
        clock=clock,
        lane_factory=lambda name, idle, interval: InlineLane(name),
        zones=ZoneResolver({"TZ": "Europe/Berlin"}),
    )
    rt.poll_transport()
    return rt


def enable(runtime, *games, level=2, minutes=15, dance_tile=None):
    runtime.store.set_games(
        {
            "daily_minutes": minutes,
            "dance_tile": dance_tile,
            "items": {game: {"enabled": True, "level": level} for game in games},
        }
    )


def add_tile(runtime, fake, index):
    favorite = fake.favorites[index]
    source = favorite_source(favorite.item_id, favorite.ref, favorite.route, favorite.description)
    return runtime.library.add(favorite.title, source)


def refused(call):
    with pytest.raises(Refused) as info:
        call()
    return info.value.code


def test_games_are_off_by_default(runtime):
    assert refused(lambda: runtime.start_game("sound_quiz")) == "game_unavailable"
    assert runtime.state_document()["games"]["items"] == []
    with pytest.raises(UnknownGame):
        runtime.start_game("chess")


def test_a_round_is_booked_and_unused_time_given_back(runtime, clock):
    enable(runtime, "sound_quiz", level=1)
    game = runtime.start_game("sound_quiz")
    assert game.granted == 180
    doc = runtime.state_document()["games"]
    assert doc["remaining"] == 900 - 180
    assert doc["active"] == {"id": "sound_quiz", "ends_at": int(game.ends_at)}
    assert refused(lambda: runtime.start_game("sound_quiz")) == "game_running"
    clock.advance(60)
    runtime.end_game()
    assert runtime.games.used_today() == 60
    assert runtime.state_document()["games"]["active"] is None


def test_the_daily_limit(runtime, clock):
    enable(runtime, "move_like", minutes=5)
    runtime.start_game("move_like")  # 180 s of 300
    clock.advance(200)
    runtime.end_game()
    assert runtime.start_game("move_like").granted == 120  # only what is left
    clock.advance(130)
    runtime.end_game()
    assert refused(lambda: runtime.start_game("move_like")) == "games_limit_reached"
    assert runtime.state_document()["games"]["items"][0]["available"] is False


def test_a_new_day_starts_with_full_game_time(runtime, clock):
    enable(runtime, "move_like", minutes=5)
    runtime.start_game("move_like")
    runtime.end_game()
    clock.set_time(datetime(2026, 9, 29, 9, 0, tzinfo=BERLIN).timestamp())
    assert runtime.games.remaining() == 300


def test_a_tablet_that_vanished_frees_the_game(runtime, clock):
    enable(runtime, "sound_quiz")
    runtime.start_game("sound_quiz")
    clock.advance(241)
    assert runtime.games.active() is None
    runtime.start_game("sound_quiz")


def test_games_end_before_the_bedtime_fade(runtime, clock):
    runtime.store.set_schedule(
        {
            "enabled": True,
            "fade_minutes": 10,
            "days": {day: {"from": "07:00", "to": "19:00"} for day in ("mon", "tue")},
        }
    )
    enable(runtime, "sound_quiz", level=3)
    clock.set_time(datetime(2026, 9, 28, 18, 47, tzinfo=BERLIN).timestamp())
    assert runtime.start_game("sound_quiz").granted == 180  # until 18:50
    runtime.end_game()
    clock.set_time(datetime(2026, 9, 28, 18, 49, 30, tzinfo=BERLIN).timestamp())
    assert refused(lambda: runtime.start_game("sound_quiz")) == "game_unavailable"
    clock.set_time(datetime(2026, 9, 28, 20, 0, tzinfo=BERLIN).timestamp())
    assert refused(lambda: runtime.start_game("sound_quiz")) == "bedtime"


def test_breathing_is_free_and_allowed_at_bedtime(runtime, clock):
    runtime.store.set_schedule({"enabled": True, "days": {"mon": {"from": "07:00", "to": "08:00"}}})
    enable(runtime, "breathing")
    game = runtime.start_game("breathing")
    assert (game.granted, runtime.games.used_today()) == (0.0, 0.0)
    assert runtime.state_document()["games"]["items"] == [
        {"id": "breathing", "level": 2, "available": True}
    ]


def test_quiz_pauses_the_music(runtime, fake_sonos):
    enable(runtime, "sound_quiz")
    tile = add_tile(runtime, fake_sonos, 0)
    runtime.play_tile(tile.id)
    runtime.start_game("sound_quiz")
    assert fake_sonos.state == "paused"


def test_freeze_dance_plays_the_dance_tile_and_mutes(runtime, fake_sonos, clock):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    runtime.start_game("freeze_dance")
    assert fake_sonos.state == "playing"
    runtime.game_mute(True)
    assert fake_sonos.muted is True
    assert runtime.timers.state.game_mute is True
    runtime.game_mute(False)
    assert fake_sonos.muted is False
    runtime.game_mute(True)
    runtime.end_game()
    assert (fake_sonos.muted, fake_sonos.state) == (False, "paused")


def test_a_mute_the_tablet_stops_renewing_ends_by_itself(runtime, fake_sonos, clock):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    runtime.start_game("freeze_dance")
    runtime.game_mute(True)
    runtime.poll_volume()
    assert fake_sonos.muted is True  # within the lease
    clock.advance(MUTE_LEASE + 1)
    runtime.poll_volume()
    assert fake_sonos.muted is False


def test_freeze_dance_needs_music(runtime):
    enable(runtime, "freeze_dance")
    assert refused(lambda: runtime.start_game("freeze_dance")) == "dance_music_missing"
    assert runtime.games.active() is None
    assert runtime.games.used_today() == 0


def test_mute_only_during_the_freeze_dance(runtime):
    assert refused(lambda: runtime.game_mute(True)) == "game_unavailable"


def test_choosing_another_room_ends_the_game_and_unmutes(runtime, fake_sonos):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    runtime.start_game("freeze_dance")
    runtime.game_mute(True)
    fake_sonos.fail_next["set_mute"] = SonosUnreachable()  # the old room hiccups once
    runtime.choose_room("Wohnzimmer", None)
    assert runtime.games.active() is None
    runtime.poll_volume()
    assert fake_sonos.muted is True  # not given up ...
    runtime.poll_volume()
    assert fake_sonos.muted is False  # ... the room the game muted is unmuted
    assert runtime.timers.state.game_mute is False


def test_ending_the_freeze_dance_just_after_it_ran_out_still_stops_the_music(
    runtime, fake_sonos, clock
):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    game = runtime.start_game("freeze_dance")
    clock.advance(game.granted + 3)  # "Fertig!" is still being said
    runtime.state_document()  # the kids view polled meanwhile
    runtime.end_game()
    assert fake_sonos.state == "paused"


def test_freeze_dance_ends_when_its_music_fails(runtime, fake_sonos):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    fake_sonos.fail_next["play_favorite"] = ServiceUnavailable(upnp_code=800)
    with pytest.raises(Unavailable) as info:
        runtime.start_game("freeze_dance")
    assert info.value.code == "service_unavailable"
    assert runtime.games.active() is None  # the start failed: game over
    assert runtime.games.used_today() == 0  # and its time came back
    assert refused(lambda: runtime.game_mute(True)) == "game_unavailable"


def test_the_end_pauses_only_music_the_dance_started(runtime, fake_sonos):
    enable(runtime, "freeze_dance")  # no dance tile: dance to what plays
    fake_sonos.media_uri, fake_sonos.state = "x-sonosapi-stream:living-room", "playing"
    runtime.poll_transport()
    runtime.state.update(sonos={"status": "ok", "room": "Kinderzimmer", "grouped": True})
    runtime.start_game("freeze_dance")
    runtime.end_game()
    assert fake_sonos.state == "playing"  # the living room's music goes on


def test_the_game_mute_flag_is_saved_before_muting(runtime, fake_sonos, monkeypatch):
    tile = add_tile(runtime, fake_sonos, 0)
    enable(runtime, "freeze_dance", dance_tile=tile.id)
    runtime.start_game("freeze_dance")
    order = []
    real_save, real_mute = runtime.timers.save, fake_sonos.set_mute
    monkeypatch.setattr(runtime.timers, "save", lambda: order.append("save") or real_save())
    monkeypatch.setattr(fake_sonos, "set_mute", lambda m: order.append(f"mute {m}") or real_mute(m))
    runtime.game_mute(True)
    runtime.game_mute(False)
    runtime.game_mute(True)
    assert order == ["save", "mute True", "mute False", "mute True"]  # saved once, first
    assert runtime.timers.state.game_mute is True  # until the game is over


def test_games_are_not_offered_when_they_would_be_refused(runtime, clock):
    runtime.store.set_schedule({"enabled": True, "days": {"mon": {"from": "07:00", "to": "19:00"}}})
    enable(runtime, "sound_quiz")
    clock.set_time(datetime(2026, 9, 28, 18, 49, 30, tzinfo=BERLIN).timestamp())
    assert runtime.state_document()["games"]["items"][0]["available"] is False
    assert refused(lambda: runtime.start_game("sound_quiz")) == "game_unavailable"
