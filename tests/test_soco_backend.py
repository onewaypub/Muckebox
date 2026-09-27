# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest
import requests
from soco.exceptions import SoCoSlaveException, SoCoUPnPException

from muckebox.sonos import soco_backend
from muckebox.sonos.errors import (
    ActionNotAvailable,
    CommandRejected,
    GroupProblem,
    NotPlayable,
    RoomNotFound,
    ServiceUnavailable,
    SonosTimeout,
    SonosUnreachable,
    UpnpDisabled,
)
from muckebox.sonos.model import FavoriteRef, Route, ShareLinkRef
from muckebox.sonos.soco_backend import (
    REQUEST_TIMEOUT,
    SLOW_TIMEOUT,
    VOLUME_TIMEOUT,
    SocoBackend,
    translate,
)
from tests.fakesoco import (
    ALBUM_URI,
    NAS_URI,
    RADIO_URI,
    FakeZone,
    household,
    parsed_favorites,
    res_md,
    switch_off,
    zone_group_state,
)


def upnp_error(code):
    return SoCoUPnPException(f"UPnP Error {code}", str(code), "<error/>")


@pytest.fixture
def kids():
    return FakeZone("Kinderzimmer", "192.0.2.10", "RINCON_000000000000001400")


@pytest.fixture
def living():
    return FakeZone("Wohnzimmer", "192.0.2.11", "RINCON_000000000001001400")


def backend_for(zones, room="Kinderzimmer", seed="192.0.2.11", **kwargs):
    return SocoBackend(
        room=room,
        seed_ip=seed,
        soco_factory=lambda ip: zones[ip],
        resolve_host=lambda host: host,
        **kwargs,
    )


# -- resolving the room -------------------------------------------------------


def test_seed_ip_and_room_name_select_the_room(kids, living):
    zones = household(kids, living)
    info = backend_for(zones, room="  kinderZIMMER ").resolve()
    assert (info.name, info.player_ip, info.coordinator_ip) == (
        "Kinderzimmer",
        "192.0.2.10",
        "192.0.2.10",
    )
    assert not info.grouped


def test_seed_ip_alone_selects_the_seed_room(kids, living):
    zones = household(kids, living)
    assert backend_for(zones, room=None, seed="192.0.2.10").resolve().name == "Kinderzimmer"


def test_invisible_stereo_partner_maps_to_its_visible_room(kids, living):
    partner = FakeZone("Kinderzimmer", "192.0.2.12", "RINCON_000000000002001400", visible=False)
    zones = household(kids, living, partner, groups=[(kids, kids, partner), (living, living)])
    info = backend_for(zones, room=None, seed="192.0.2.12").resolve()
    assert info.player_ip == "192.0.2.10"


def test_grouped_room_reports_coordinator(kids, living):
    zones = household(kids, living, groups=[(living, living, kids)])
    info = backend_for(zones).resolve()
    assert info.coordinator_ip == "192.0.2.11"
    assert info.grouped


def test_unknown_room(kids, living):
    zones = household(kids, living)
    with pytest.raises(RoomNotFound):
        backend_for(zones, room="Keller").resolve()


def test_room_by_discovery_without_ip(kids, living):
    zones = household(kids, living)
    backend = SocoBackend(
        room="Kinderzimmer",
        seed_ip=None,
        discover=lambda timeout, allow_network_scan: {kids, living},
        soco_factory=lambda ip: zones[ip],
    )
    assert backend.resolve().player_ip == "192.0.2.10"


def test_discovery_finding_nothing(kids):
    seen = {}

    def discover(**kwargs):
        seen.update(kwargs)

    backend = SocoBackend(room="Kinderzimmer", seed_ip=None, discover=discover)
    with pytest.raises(RoomNotFound):
        backend.resolve()
    assert seen["allow_network_scan"] is True  # falls back to scanning the network


def test_unreachable_seed(kids):
    zones = household(kids)
    switch_off(kids)
    with pytest.raises(SonosUnreachable):
        backend_for(zones, seed="192.0.2.10").resolve()


def test_missing_coordinator_is_a_group_problem(kids):
    zones = household(kids)
    xml = zone_group_state([(FakeZone("Gone", "192.0.2.99", "RINCON_000000000099001400"), kids)])
    kids.zoneGroupTopology.responses["GetZoneGroupState"] = {"ZoneGroupState": xml}
    with pytest.raises(GroupProblem):
        backend_for(zones, seed="192.0.2.10").resolve()


def test_resolving_asks_one_speaker_once_without_service_descriptions(kids, living):
    zones = household(kids, living)
    backend_for(zones).resolve()
    assert living.zoneGroupTopology.calls == [("GetZoneGroupState", {}, REQUEST_TIMEOUT)]
    assert kids.zoneGroupTopology.calls == []  # other rooms are never contacted


def test_seed_speaker_off_keeps_the_kids_room_under_control(kids, living):
    """The seed (e.g. the living room) goes off; the kids room stays reachable."""
    zones = household(kids, living)
    backend = backend_for(zones, seed="192.0.2.11")
    backend.resolve()
    switch_off(living)
    info = backend.resolve()  # asks the kids room itself now
    assert info.player_ip == "192.0.2.10"
    kids.renderingControl.responses["GetVolume"] = {"CurrentVolume": "80"}
    assert backend.get_volume() == 80
    backend.set_volume(25)
    assert kids.renderingControl.calls[-1][1]["DesiredVolume"] == 25


def test_failed_lookup_keeps_the_last_known_room(kids, living):
    zones = household(kids, living)
    backend = backend_for(zones, seed="192.0.2.11")
    backend.resolve()
    switch_off(kids)
    switch_off(living)
    with pytest.raises(SonosUnreachable):
        backend.resolve()
    kids.renderingControl.errors = {}  # the kids room answers again
    assert backend.get_volume() == 12  # no new lookup needed


def test_seed_host_name_that_does_not_resolve(kids):
    def no_dns(host):
        raise OSError("name not known")

    backend = SocoBackend(room="Kinderzimmer", seed_ip="speaker.example", resolve_host=no_dns)
    with pytest.raises(SonosUnreachable):
        backend.resolve()


def test_unreadable_zone_group_state(kids):
    zones = household(kids)
    kids.zoneGroupTopology.responses["GetZoneGroupState"] = {"ZoneGroupState": "<broken"}
    with pytest.raises(CommandRejected):
        backend_for(zones, seed="192.0.2.10").resolve()


# -- favorites ----------------------------------------------------------------


def test_list_favorites_classifies_each_item(kids):
    zones = household(kids)
    kids.music_library.get_sonos_favorites = lambda **kwargs: parsed_favorites()
    favorites = {f.title: f for f in backend_for(zones, seed="192.0.2.10").list_favorites()}

    album = favorites["Bibi & Tina"]
    assert (album.route, album.playable, album.description) == (Route.QUEUE, True, "Apple Music")
    assert album.ref.uri == ALBUM_URI
    assert album.ref.item_class == "object.container.album.musicAlbum"
    assert album.art_uri.startswith("http://192.0.2.10:1400/getaa?")

    assert favorites["Kinderradio"].route is Route.DIRECT
    # NAS favorites without a class stay playable (library fallback).
    assert favorites["NAS Kids"].route is Route.QUEUE
    assert (favorites["Artist shortcut"].route, favorites["Artist shortcut"].reason) == (
        Route.UNSUPPORTED,
        "no_resource",
    )
    assert favorites["Broken service item"].reason == "broken_metadata"


def test_favorites_request_complete_result(kids):
    zones = household(kids)
    seen = {}
    kids.music_library.get_sonos_favorites = lambda **kwargs: seen.update(kwargs) or []
    backend_for(zones, seed="192.0.2.10").list_favorites()
    assert seen == {"complete_result": True}


# -- playback -----------------------------------------------------------------

ALBUM = FavoriteRef(
    uri=ALBUM_URI,
    protocol_info="x-rincon-cpcontainer:*:*:*",
    res_md=res_md("1004206calbum%3a1001", "Bibi & Tina", "object.container.album.musicAlbum"),
    item_class="object.container.album.musicAlbum",
    title="Bibi & Tina",
)
RADIO = FavoriteRef(
    uri=RADIO_URI,
    protocol_info="x-sonosapi-stream:*:*:*",
    res_md=res_md("F00092020s00001", "Kinderradio", "object.item.audioItem.audioBroadcast"),
    item_class="object.item.audioItem.audioBroadcast",
    title="Kinderradio",
)


def test_queue_route_on_the_group_coordinator(kids, living):
    zones = household(kids, living, groups=[(living, living, kids)])
    route = backend_for(zones).play_favorite(ALBUM, Route.QUEUE)
    assert route is Route.QUEUE
    assert kids.avTransport.calls == []  # never the grouped member
    assert living.avTransport.actions() == [
        "RemoveAllTracksFromQueue",
        "AddURIToQueue",
        "SetAVTransportURI",
        "SetPlayMode",  # once the queue is the source, so Sonos accepts it
        "Seek",
        "Play",
    ]
    calls = {name: (args, timeout) for name, args, timeout in living.avTransport.calls}
    assert calls["SetPlayMode"][0]["NewPlayMode"] == "NORMAL"
    add_args, add_timeout = calls["AddURIToQueue"]
    assert add_args["EnqueuedURI"] == ALBUM_URI
    assert "Bibi &amp; Tina" in add_args["EnqueuedURIMetaData"]
    assert ALBUM_URI.replace("&", "&amp;") in add_args["EnqueuedURIMetaData"]
    assert add_timeout == SLOW_TIMEOUT
    assert (
        calls["SetAVTransportURI"][0]["CurrentURI"] == "x-rincon-queue:RINCON_000000000001001400#0"
    )
    assert calls["Seek"][0] == {"InstanceID": 0, "Unit": "TRACK_NR", "Target": 1}


def test_direct_route_never_touches_the_queue(kids):
    zones = household(kids)
    assert backend_for(zones, seed="192.0.2.10").play_favorite(RADIO, Route.DIRECT) is Route.DIRECT
    assert kids.avTransport.actions() == ["SetAVTransportURI", "Play"]
    args = kids.avTransport.calls[0][1]
    assert args["CurrentURI"] == RADIO_URI
    assert args["CurrentURIMetaData"] == RADIO.res_md


def test_route_error_switches_route_once(kids):
    zones = household(kids)
    kids.avTransport.errors["SetAVTransportURI"] = [upnp_error(714)]
    route = backend_for(zones, seed="192.0.2.10").play_favorite(RADIO, Route.DIRECT)
    assert route is Route.QUEUE
    assert "AddURIToQueue" in kids.avTransport.actions()


def test_other_errors_do_not_switch_route(kids):
    zones = household(kids)
    kids.avTransport.errors["AddURIToQueue"] = [upnp_error(800), upnp_error(800)]
    with pytest.raises(ServiceUnavailable):
        backend_for(zones, seed="192.0.2.10").play_favorite(ALBUM, Route.QUEUE)
    assert "Play" not in kids.avTransport.actions()


def test_empty_queue_error_on_clear_is_ignored(kids):
    zones = household(kids)
    kids.avTransport.errors["RemoveAllTracksFromQueue"] = [upnp_error(804)]
    backend_for(zones, seed="192.0.2.10").play_favorite(ALBUM, Route.QUEUE)
    assert kids.avTransport.actions()[-1] == "Play"


def test_slow_enqueue_that_filled_the_queue_still_plays(kids):
    zones = household(kids)
    kids.avTransport.errors["AddURIToQueue"] = [requests.exceptions.ReadTimeout()]
    kids.contentDirectory.responses["Browse"] = {"Result": "", "TotalMatches": "12"}
    backend_for(zones, seed="192.0.2.10").play_favorite(ALBUM, Route.QUEUE)
    assert kids.avTransport.actions()[-1] == "Play"


def test_slow_enqueue_with_empty_queue_fails(kids):
    zones = household(kids)
    kids.avTransport.errors["AddURIToQueue"] = [requests.exceptions.ReadTimeout()]
    with pytest.raises(SonosTimeout):
        backend_for(zones, seed="192.0.2.10").play_favorite(ALBUM, Route.QUEUE)


def test_library_favorite_without_class_uses_bare_item(kids):
    zones = household(kids)
    ref = FavoriteRef(NAS_URI, "x-rincon-playlist:*:*:*", res_md("S://x", "NAS", ""), "", "NAS")
    backend_for(zones, seed="192.0.2.10").play_favorite(ref, Route.QUEUE)
    add = next(c for c in kids.avTransport.calls if c[0] == "AddURIToQueue")
    assert add[1]["EnqueuedURI"] == NAS_URI


def test_service_favorite_with_broken_metadata_is_not_playable(kids):
    zones = household(kids)
    ref = FavoriteRef("x-sonos-http:song%3a1.mp4?sid=204", "", "<broken", "", "Broken")
    with pytest.raises(NotPlayable):
        backend_for(zones, seed="192.0.2.10").play_favorite(ref, Route.QUEUE)


def test_unsupported_route_is_refused(kids):
    zones = household(kids)
    with pytest.raises(NotPlayable):
        backend_for(zones, seed="192.0.2.10").play_favorite(RADIO, Route.UNSUPPORTED)


def test_share_link_is_enqueued_with_escaped_title(kids):
    zones = household(kids)
    link = ShareLinkRef("apple_music", "album", "1440857781")
    backend_for(zones, seed="192.0.2.10").play_share_link(link, "Bibi & Tina <3")
    add = next(c for c in kids.avTransport.calls if c[0] == "AddURIToQueue")
    assert add[1]["EnqueuedURI"] == "x-rincon-cpcontainer:1004206calbum%3a1440857781"
    assert "Bibi &amp; Tina &lt;3" in add[1]["EnqueuedURIMetaData"]
    assert "SA_RINCON52231_X_#Svc52231-0-Token" in add[1]["EnqueuedURIMetaData"]
    assert add[2] == SLOW_TIMEOUT
    assert kids.avTransport.actions()[-1] == "Play"


# -- transport ----------------------------------------------------------------


def test_pause_falls_back_to_stop_on_radio(kids):
    zones = household(kids)
    kids.avTransport.responses["GetCurrentTransportActions"] = {"Actions": "Set, Stop, Play"}
    backend_for(zones, seed="192.0.2.10").transport("pause")
    assert kids.avTransport.actions()[-1] == "Stop"


def test_next_without_queue_is_not_available(kids):
    zones = household(kids)
    kids.avTransport.errors["Next"] = [upnp_error(701)]
    with pytest.raises(ActionNotAvailable):
        backend_for(zones, seed="192.0.2.10").transport("next")


def test_unknown_transport_action(kids):
    zones = household(kids)
    with pytest.raises(ValueError):
        backend_for(zones, seed="192.0.2.10").transport("eject")


def test_playback_reads_state_and_first_queue_item(kids):
    zones = household(kids)
    kids.avTransport.responses["GetMediaInfo"] = {"CurrentURI": "x-rincon-queue:RINCON_1#0"}
    kids.contentDirectory.responses["Browse"] = {
        "Result": (
            '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
            '<item id="Q:0/1" parentID="Q:0" restricted="true">'
            '<res protocolInfo="sonos.com-http:*:audio/mp4:*">x-sonos-http:song%3a1.mp4</res>'
            "<dc:title>Track</dc:title><upnp:class>object.item.audioItem.musicTrack</upnp:class>"
            "</item></DIDL-Lite>"
        ),
        "TotalMatches": "10",
    }
    playback = backend_for(zones, seed="192.0.2.10").playback()
    assert playback.state == "playing"
    assert playback.first_queue_uri == "x-sonos-http:song%3a1.mp4"
    assert playback.can_next and playback.can_prev and playback.can_pause


# -- volume -------------------------------------------------------------------


def test_volume_uses_room_player_and_short_timeout(kids, living):
    zones = household(kids, living, groups=[(living, living, kids)])
    backend = backend_for(zones)
    assert backend.get_volume() == 12
    backend.set_volume(30)
    assert living.renderingControl.calls == []
    set_call = kids.renderingControl.calls[-1]
    assert set_call[0] == "SetVolume"
    assert set_call[1]["DesiredVolume"] == 30
    assert set_call[2] == VOLUME_TIMEOUT


def test_fixed_volume_detection(kids):
    zones = household(kids)
    backend = backend_for(zones, seed="192.0.2.10")
    assert backend.fixed_volume() is False
    kids.renderingControl.responses["GetSupportsOutputFixed"] = {"CurrentSupportsFixed": "1"}
    kids.renderingControl.responses["GetOutputFixed"] = {"CurrentFixed": "1"}
    assert backend.fixed_volume() is True


# -- album art ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, chunks, status=200):
        self.chunks = chunks
        self.status_code = status

    def iter_content(self, size):
        return iter(self.chunks)

    def close(self):
        pass


def test_relative_art_is_fetched_from_the_speaker(kids):
    zones = household(kids)
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        return FakeResponse([b"abc", b"def"])

    backend = backend_for(zones, seed="192.0.2.10", http_get=get)
    assert backend.fetch_art("/getaa?s=1&u=x") == b"abcdef"
    assert urls == ["http://192.0.2.10:1400/getaa?s=1&u=x"]


def test_art_size_is_limited(kids):
    zones = household(kids)
    backend = backend_for(
        zones, seed="192.0.2.10", http_get=lambda url, **kw: FakeResponse([b"x" * 6_000_000] * 2)
    )
    backend.resolve()
    with pytest.raises(Exception, match="too large"):
        backend.fetch_art("http://192.0.2.10:1400/getaa")


def test_art_needs_http(kids):
    zones = household(kids)
    with pytest.raises(NotPlayable):
        backend_for(zones, seed="192.0.2.10").fetch_art("file:///etc/passwd")


# -- error translation ---------------------------------------------------------------


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


@pytest.mark.parametrize(
    ("exc", "expected", "connection_problem"),
    [
        (requests.exceptions.ConnectTimeout(), SonosUnreachable, True),
        (requests.exceptions.ConnectionError(), SonosUnreachable, True),
        (requests.exceptions.ReadTimeout(), SonosTimeout, False),
        (http_error(403), UpnpDisabled, True),
        (upnp_error(800), ServiceUnavailable, False),
        (upnp_error(701), ActionNotAvailable, False),
        (SoCoSlaveException("not coordinator"), GroupProblem, False),
        (OSError("no route to host"), SonosUnreachable, True),
    ],
)
def test_translate(exc, expected, connection_problem):
    error = translate(exc)
    assert isinstance(error, expected)
    assert error.connection_problem is connection_problem


def test_translate_keeps_upnp_code():
    assert translate(upnp_error(714)).upnp_code == 714


def test_translate_reraises_programming_errors():
    with pytest.raises(KeyError):
        translate(KeyError("bug"))


# -- review regressions ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(soco_backend, "RETRY_PAUSE", 0)


def test_one_transient_service_error_is_retried(kids):
    zones = household(kids)
    kids.avTransport.errors["AddURIToQueue"] = [upnp_error(800)]
    backend_for(zones, seed="192.0.2.10").play_favorite(ALBUM, Route.QUEUE)
    actions = kids.avTransport.actions()
    assert actions.count("AddURIToQueue") == 2
    assert actions.count("RemoveAllTracksFromQueue") == 2  # a half-filled queue is cleared
    assert actions[-1] == "Play"


def test_play_during_transition_is_retried(kids):
    zones = household(kids)
    kids.avTransport.errors["Play"] = [upnp_error(701)]
    backend_for(zones, seed="192.0.2.10").play_favorite(RADIO, Route.DIRECT)
    assert kids.avTransport.actions().count("Play") == 2


def test_play_refused_twice_fails(kids):
    from muckebox.sonos.errors import PlaybackFailed

    zones = household(kids)
    kids.avTransport.errors["Play"] = [upnp_error(701), upnp_error(701)]
    with pytest.raises(PlaybackFailed):
        backend_for(zones, seed="192.0.2.10").play_favorite(RADIO, Route.DIRECT)


def test_odd_favorites_never_break_the_list(kids):
    from soco.data_structures_entry import from_didl_string

    from tests.fakesoco import NS, favorite_xml

    xml = (
        f"<DIDL-Lite {NS}>"
        + favorite_xml(1, "Empty metadata", "x-sonos-http:song%3a1.mp4?sid=204", "")
        + favorite_xml(2, "Garbage library", NAS_URI, "no xml at all")
        + "</DIDL-Lite>"
    )
    zones = household(kids)
    kids.music_library.get_sonos_favorites = lambda **kwargs: from_didl_string(xml)
    favorites = {f.title: f for f in backend_for(zones, seed="192.0.2.10").list_favorites()}
    assert favorites["Empty metadata"].reason == "broken_metadata"
    library = favorites["Garbage library"]
    assert library.playable
    assert library.ref.res_md == ""  # broken metadata is never stored in a tile


def test_speaker_art_is_fetched_without_redirects(kids):
    zones = household(kids)
    options = {}

    def get(url, **kwargs):
        options.update(kwargs)
        return FakeResponse([b"img"])

    backend = backend_for(zones, seed="192.0.2.10", http_get=get)
    backend.resolve()
    assert backend.fetch_art("http://192.0.2.10:1400/getaa?u=x") == b"img"
    assert options["allow_redirects"] is False


def test_other_art_goes_through_the_restricted_fetcher(kids):
    from muckebox.netfetch import FetchError, FetchResult

    zones = household(kids)
    calls = []

    class Fetcher:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if "lan" in url:
                raise FetchError("address_not_allowed")
            return FetchResult(url=url, status=200, headers={}, body=b"cdn")

        def close(self):
            pass

    backend = backend_for(zones, seed="192.0.2.10", fetcher_factory=Fetcher)
    backend.resolve()
    assert backend.fetch_art("https://images.example.com/cover.jpg") == b"cdn"
    assert calls[0][1]["allow_http"] is True
    # A speaker-looking URL on an address that is not one of our speakers is
    # not trusted: it goes through the fetcher and its address checks.
    with pytest.raises(CommandRejected):
        backend.fetch_art("http://lan.example:1400/getaa")
