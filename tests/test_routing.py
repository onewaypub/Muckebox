# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.sonos.model import Route
from muckebox.sonos.routing import classify, other_route

ALBUM = "object.container.album.musicAlbum"
PLAYLIST = "object.container.playlistContainer"
TRACK = "object.item.audioItem.musicTrack"
BROADCAST = "object.item.audioItem.audioBroadcast"


@pytest.mark.parametrize(
    ("uri", "item_class", "expected"),
    [
        # Radio and line-in always play directly.
        ("x-sonosapi-stream:s24896?sid=254&flags=8224&sn=0", BROADCAST, Route.DIRECT),
        ("x-sonosapi-radio:ST%3aabc?sid=236&flags=8300&sn=1", PLAYLIST, Route.DIRECT),
        ("x-sonosapi-hls:radio?sid=303", "", Route.DIRECT),
        ("x-rincon-mp3radio://radio.example.com/live.mp3", "", Route.DIRECT),
        ("aac://radio.example.com/stream", "", Route.DIRECT),
        ("hls-radio://radio.example.com/playlist.m3u8", "", Route.DIRECT),
        ("x-sonos-http:sonos%3aradio?sid=303", "", Route.DIRECT),
        ("x-rincon-stream:RINCON_000000000000001400", "", Route.DIRECT),
        # Broadcast class with a non-radio URI (e.g. a service's live station).
        ("x-sonos-http:station%3a123.mp4?sid=204", BROADCAST, Route.DIRECT),
        # Audiobooks and podcast episodes resume, so they play directly.
        ("x-sonos-http:book%3a1.mp4?sid=239", "object.item.audioItem.audioBook", Route.DIRECT),
        (
            "x-sonos-http:ep%3a2.mp3?sid=204",
            "object.item.audioItem.musicTrack.recentShow",
            Route.DIRECT,
        ),
        ("x-sonos-http:ep%3a3.mp3?sid=204", "object.item.audioItem.podcast", Route.DIRECT),
        # Containers and tracks go through the queue.
        ("x-rincon-cpcontainer:1004206calbum%3a1440?sid=204&flags=8300&sn=3", ALBUM, Route.QUEUE),
        ("x-rincon-cpcontainer:1006206cplaylist%3apl.1?sid=204", PLAYLIST, Route.QUEUE),
        (
            "x-rincon-cpcontainer:1006206cpodcast%3a9?sid=204",
            "object.container.podcast",
            Route.QUEUE,
        ),
        ("x-sonos-http:song%3a123.mp4?sid=204&flags=8224&sn=3", TRACK, Route.QUEUE),
        ("x-rincon-playlist:RINCON_000000000000001400#S://nas/music/Kids", "", Route.QUEUE),
        ("x-file-cifs://nas/music/Kids/track01.mp3", TRACK, Route.QUEUE),
        ("file:///jffs/settings/savedqueues.rsq#12", PLAYLIST, Route.QUEUE),
    ],
)
def test_routes(uri, item_class, expected):
    route, reason = classify(uri, item_class)
    assert route is expected
    assert reason is None


def test_tv_input_is_unsupported():
    assert classify("x-sonos-htastream:RINCON_000000000000001400:spdif", "") == (
        Route.UNSUPPORTED,
        "tv_input",
    )


def test_missing_uri_is_unsupported():
    assert classify("", ALBUM) == (Route.UNSUPPORTED, "no_resource")


def test_other_route():
    assert other_route(Route.DIRECT) is Route.QUEUE
    assert other_route(Route.QUEUE) is Route.DIRECT
