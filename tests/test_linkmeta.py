# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from urllib.parse import quote

import pytest
import requests
import requests_mock

from muckebox.linkmeta import (
    UNRESOLVABLE,
    UNSUPPORTED,
    LinkError,
    ParsedLink,
    ShortLinkError,
    parse,
    resolve,
)
from muckebox.netfetch import Fetcher
from muckebox.sonos.model import ShareLinkRef
from muckebox.sonos.sharelink import plugin_uri

# A public anycast address; the fake resolver answers every host with it.
PUBLIC_V4 = "1.1.1.1"

# Made-up IDs in the services' formats.
SP_ALBUM = "0ExampleAlbum000000001"
SP_TRACK = "0ExampleTrack000000002"
SP_PLAYLIST = "0ExamplePlaylist000003"
SP_SHOW = "0ExampleShow0000000004"
SP_EPISODE = "0ExampleEpisode0000005"
SP_ARTIST = "0ExampleArtist00000006"
AP_ALBUM = "1000000001"
AP_SONG = "1000000002"
AP_PLAYLIST = "pl.00000000000000000000000000000001"
AP_USER_PLAYLIST = "pl.u-Ex4mpleL1st"
TD_ALBUM = "100000001"
TD_TRACK = "100000002"
TD_PLAYLIST = "00000000-0000-4000-8000-000000000001"
DZ_ALBUM = "200000001"
DZ_TRACK = "200000002"
DZ_PLAYLIST = "200000003"

SP = "https://open.spotify.com"
AP = "https://music.apple.com"


@pytest.fixture
def web():
    with requests_mock.Mocker() as mocker:
        yield mocker


@pytest.fixture
def fetcher():
    return Fetcher(resolver=lambda host, port: [PUBLIC_V4])


def link(service, kind, item_id, url):
    return ParsedLink(ShareLinkRef(service, kind, item_id), url)


def test_example_spotify_ids_have_the_real_length():
    for item_id in (SP_ALBUM, SP_TRACK, SP_PLAYLIST, SP_SHOW, SP_EPISODE, SP_ARTIST):
        assert len(item_id) == 22


# -- parse -------------------------------------------------------------------------

PARSE_CASES = [
    # Spotify
    (f"{SP}/album/{SP_ALBUM}", "spotify", "album", SP_ALBUM, f"{SP}/album/{SP_ALBUM}"),
    (f"{SP}/album/{SP_ALBUM}?si=Ab12Cd34", "spotify", "album", SP_ALBUM, None),
    (f"{SP}/intl-de/album/{SP_ALBUM}?si=Ab12", "spotify", "album", SP_ALBUM, None),
    (f"{SP}/intl-pt-BR/track/{SP_TRACK}", "spotify", "track", SP_TRACK, None),
    (
        f"{SP}/track/{SP_TRACK}?context=spotify:album:{SP_ALBUM}",
        "spotify",
        "track",
        SP_TRACK,
        f"{SP}/track/{SP_TRACK}",
    ),
    (
        f"{SP}/track/{SP_TRACK}?si=x&context=spotify%3Aalbum%3A{SP_ALBUM}",
        "spotify",
        "track",
        SP_TRACK,
        None,
    ),
    (f"{SP}/playlist/{SP_PLAYLIST}?si=x&pi=y", "spotify", "playlist", SP_PLAYLIST, None),
    (f"{SP}/episode/{SP_EPISODE}", "spotify", "episode", SP_EPISODE, f"{SP}/episode/{SP_EPISODE}"),
    (f"{SP}/show/{SP_SHOW}?si=1", "spotify", "show", SP_SHOW, f"{SP}/show/{SP_SHOW}"),
    (f"{SP}/embed/album/{SP_ALBUM}", "spotify", "album", SP_ALBUM, None),
    (f"{SP}/user/example/playlist/{SP_PLAYLIST}", "spotify", "playlist", SP_PLAYLIST, None),
    (f"https://play.spotify.com/album/{SP_ALBUM}/", "spotify", "album", SP_ALBUM, None),
    (
        f"http://OPEN.SPOTIFY.COM/Album/{SP_ALBUM}",
        "spotify",
        "album",
        SP_ALBUM,
        f"{SP}/album/{SP_ALBUM}",
    ),
    (f"spotify:album:{SP_ALBUM}", "spotify", "album", SP_ALBUM, f"{SP}/album/{SP_ALBUM}"),
    (f"spotify:user:example:playlist:{SP_PLAYLIST}", "spotify", "playlist", SP_PLAYLIST, None),
    (f"Hör dir das an: {SP}/album/{SP_ALBUM}?si=Ab12.", "spotify", "album", SP_ALBUM, None),
    (f"Neues Lieblingslied (spotify:track:{SP_TRACK})", "spotify", "track", SP_TRACK, None),
    # Apple Music
    (
        f"{AP}/de/album/bibi-und-tina/{AP_ALBUM}",
        "apple_music",
        "album",
        AP_ALBUM,
        f"{AP}/de/album/bibi-und-tina/{AP_ALBUM}",
    ),
    (f"{AP}/de/album/{AP_ALBUM}", "apple_music", "album", AP_ALBUM, f"{AP}/de/album/{AP_ALBUM}"),
    (
        f"{AP}/album/beispiel/{AP_ALBUM}",
        "apple_music",
        "album",
        AP_ALBUM,
        f"{AP}/us/album/beispiel/{AP_ALBUM}",
    ),
    (
        f"{AP}/us/album/some-album/{AP_ALBUM}?i={AP_SONG}",
        "apple_music",
        "song",
        AP_SONG,
        f"{AP}/us/album/some-album/{AP_ALBUM}?i={AP_SONG}",
    ),
    (
        f"{AP}/de/album/some-album/{AP_ALBUM}?l=en&i={AP_SONG}&ls=1",
        "apple_music",
        "song",
        AP_SONG,
        f"{AP}/de/album/some-album/{AP_ALBUM}?i={AP_SONG}",
    ),
    (
        f"{AP}/de/album/{AP_ALBUM}?i={AP_SONG}",
        "apple_music",
        "song",
        AP_SONG,
        f"{AP}/de/album/{AP_ALBUM}?i={AP_SONG}",
    ),
    (
        f"{AP}/de/song/ein-lied/{AP_SONG}",
        "apple_music",
        "song",
        AP_SONG,
        f"{AP}/de/song/ein-lied/{AP_SONG}",
    ),
    (f"{AP}/de/song/{AP_SONG}", "apple_music", "song", AP_SONG, f"{AP}/de/song/{AP_SONG}"),
    (
        f"{AP}/us/playlist/kinderlieder/{AP_PLAYLIST}",
        "apple_music",
        "playlist",
        AP_PLAYLIST,
        f"{AP}/us/playlist/kinderlieder/{AP_PLAYLIST}",
    ),
    (
        f"{AP}/DE/playlist/meine-liste/{AP_USER_PLAYLIST}",
        "apple_music",
        "playlist",
        AP_USER_PLAYLIST,
        f"{AP}/de/playlist/meine-liste/{AP_USER_PLAYLIST}",
    ),
    (
        f"https://embed.music.apple.com/de/album/x/{AP_ALBUM}",
        "apple_music",
        "album",
        AP_ALBUM,
        f"{AP}/de/album/x/{AP_ALBUM}",
    ),
    (
        f"{AP}/de/album/über-alles/{AP_ALBUM}",
        "apple_music",
        "album",
        AP_ALBUM,
        f"{AP}/de/album/%C3%BCber-alles/{AP_ALBUM}",
    ),
    (
        f"„Bibi & Tina“ von Beispiel bei Apple Music {AP}/de/album/bibi/{AP_ALBUM}",
        "apple_music",
        "album",
        AP_ALBUM,
        None,
    ),
    # TIDAL
    (
        f"https://tidal.com/browse/album/{TD_ALBUM}",
        "tidal",
        "album",
        TD_ALBUM,
        f"https://tidal.com/album/{TD_ALBUM}",
    ),
    (f"https://tidal.com/album/{TD_ALBUM}/u", "tidal", "album", TD_ALBUM, None),
    (
        f"https://tidal.com/browse/track/{TD_TRACK}?u",
        "tidal",
        "track",
        TD_TRACK,
        f"https://tidal.com/track/{TD_TRACK}",
    ),
    (f"https://tidal.com/track/{TD_TRACK}", "tidal", "track", TD_TRACK, None),
    (f"https://listen.tidal.com/album/{TD_ALBUM}", "tidal", "album", TD_ALBUM, None),
    (
        f"https://listen.tidal.com/album/{TD_ALBUM}/track/{TD_TRACK}",
        "tidal",
        "track",
        TD_TRACK,
        None,
    ),
    (
        f"https://www.tidal.com/playlist/{TD_PLAYLIST}",
        "tidal",
        "playlist",
        TD_PLAYLIST,
        f"https://tidal.com/playlist/{TD_PLAYLIST}",
    ),
    # Deezer
    (
        f"https://www.deezer.com/de/album/{DZ_ALBUM}",
        "deezer",
        "album",
        DZ_ALBUM,
        f"https://www.deezer.com/album/{DZ_ALBUM}",
    ),
    (f"https://deezer.com/album/{DZ_ALBUM}", "deezer", "album", DZ_ALBUM, None),
    (
        f"https://www.deezer.com/track/{DZ_TRACK}?utm_source=deezer",
        "deezer",
        "track",
        DZ_TRACK,
        f"https://www.deezer.com/track/{DZ_TRACK}",
    ),
    (f"https://www.deezer.com/en/playlist/{DZ_PLAYLIST}", "deezer", "playlist", DZ_PLAYLIST, None),
    (f"https://www.deezer.com/pt-br/album/{DZ_ALBUM}", "deezer", "album", DZ_ALBUM, None),
    (
        f"Hör dir „Bibi & Tina“ auf #deezer an! https://deezer.com/de/track/{DZ_TRACK}",
        "deezer",
        "track",
        DZ_TRACK,
        None,
    ),
]


@pytest.mark.parametrize(("text", "service", "kind", "item_id", "canonical"), PARSE_CASES)
def test_parse(text, service, kind, item_id, canonical):
    parsed = parse(text)
    assert parsed.ref == ShareLinkRef(service, kind, item_id)
    if canonical is not None:
        assert parsed.canonical_url == canonical
    # Every result can be handed to the playback code.
    assert plugin_uri(parsed.ref)
    # Parsing the canonical URL again gives the same link.
    assert parse(parsed.canonical_url) == parsed


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"{SP}/artist/{SP_ARTIST}", "artist"),
        (f"{SP}/intl-de/artist/{SP_ARTIST}?si=1", "artist"),
        (f"{SP}/user/example", "user"),
        (f"{SP}/audiobook/{SP_ALBUM}", "audiobook"),
        (f"{SP}/collection/tracks", "collection"),
        (f"spotify:artist:{SP_ARTIST}", "artist"),
        ("spotify:user:example:collection", "user"),
        (f"{SP}/album/tooShort", None),
        (f"{SP}/album", None),
        (f"{SP}/", None),
        (f"{SP}/search/bibi", None),
        (f"{AP}/de/artist/beispiel/{AP_ALBUM}", "artist"),
        (f"{AP}/de/station/radio/ra.{AP_ALBUM}", "station"),
        (f"{AP}/library/playlist/p.Ex4mple", "library"),
        (f"{AP}/de/music-video/clip/{AP_ALBUM}", "music-video"),
        (f"{AP}/de/album/x/y/{AP_ALBUM}", None),
        (f"{AP}/de/album/x/{AP_ALBUM}?i=abc", None),
        (f"{AP}/de/album/x/abc", None),
        (f"{AP}/de/playlist/x/{AP_ALBUM}", None),
        (f"{AP}/de", None),
        ("https://tidal.com/browse/artist/123", "artist"),
        ("https://tidal.com/browse/mix/0123abcd", "mix"),
        ("https://tidal.com/video/123", "video"),
        ("https://tidal.com/album/abc", None),
        ("https://tidal.com/playlist/not_a_uuid", None),
        ("https://www.deezer.com/de/artist/123", "artist"),
        ("https://www.deezer.com/de/show/123", "show"),
        ("https://www.deezer.com/de/episode/123", "episode"),
        ("https://www.deezer.com/album/abc", None),
        ("https://www.deezer.com/deezer-links-404", None),
        ("https://www.youtube.com/watch?v=Ex4mple", None),
        ("https://music.example.com/album/1", None),
        ("just some words, no link", None),
        ("ftp://open.spotify.com/album/1", None),
        ("https://[broken", None),
        ("https://", None),
    ],
)
def test_unsupported_links(text, kind):
    with pytest.raises(LinkError) as info:
        parse(text)
    assert info.value.code == UNSUPPORTED
    assert info.value.kind == kind
    assert not isinstance(info.value, ShortLinkError)


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("https://spotify.link/Ex4mpleCode", "https://spotify.link/Ex4mpleCode"),
        ("Hör mal: http://spoti.fi/Ex4mple !", "https://spoti.fi/Ex4mple"),
        ("https://spotify.app.link/Ex4mple?_p=1", "https://spotify.app.link/Ex4mple?_p=1"),
        ("https://link.deezer.com/s/Ex4mpleCode", "https://link.deezer.com/s/Ex4mpleCode"),
        ("https://deezer.page.link/Ex4mple", "https://deezer.page.link/Ex4mple"),
        ("http://tidal.link/Ex4mple", "https://tidal.link/Ex4mple"),
        ("https://apple.co/Ex4mple", "https://apple.co/Ex4mple"),
        (
            f"https://geo.music.apple.com/de/album/_/{AP_ALBUM}",
            f"https://geo.music.apple.com/de/album/_/{AP_ALBUM}",
        ),
        (
            f"https://itunes.apple.com/de/album/id{AP_ALBUM}",
            f"https://itunes.apple.com/de/album/id{AP_ALBUM}",
        ),
    ],
)
def test_short_links_are_signalled(text, url):
    with pytest.raises(ShortLinkError) as info:
        parse(text)
    assert info.value.code == UNRESOLVABLE
    assert info.value.url == url


def test_first_link_in_the_text_wins():
    text = f"{SP}/track/{SP_TRACK} und spotify:album:{SP_ALBUM}"
    assert parse(text).ref.kind == "track"
    text = f"spotify:album:{SP_ALBUM} und {SP}/track/{SP_TRACK}"
    assert parse(text).ref.kind == "album"


def test_link_error_message():
    error = LinkError(UNSUPPORTED, kind="artist")
    assert str(error) == UNSUPPORTED
    assert error.kind == "artist"


# -- resolve -------------------------------------------------------------------------

SHORT = "https://spotify.link/Ex4mpleCode"
APP_LINK = "https://spotify.app.link/Ex4mpleCode?_p=c11"
BRANCH_PAGE = (
    b"<!DOCTYPE html><html><head><title>Spotify</title>"
    b'<meta property="og:title" content="Spotify"></head><body><script>'
    b'var target = "https:\\/\\/open.spotify.com\\/playlist\\/' + SP_PLAYLIST.encode() + b'?si=1";'
    b"</script></body></html>"
)


def test_resolve_does_not_touch_the_network_for_normal_links(web, fetcher):
    assert resolve(f"{SP}/album/{SP_ALBUM}", fetcher).ref.item_id == SP_ALBUM
    assert web.call_count == 0


def test_resolve_raises_parse_errors(web, fetcher):
    with pytest.raises(LinkError) as info:
        resolve(f"{SP}/artist/{SP_ARTIST}", fetcher)
    assert info.value.code == UNSUPPORTED
    assert web.call_count == 0


def test_spotify_short_link_via_head_redirect(web, fetcher):
    target = f"{SP}/track/{SP_TRACK}?si=abc&_branch_match_id=1"
    web.head(SHORT, status_code=307, headers={"Location": target})
    parsed = resolve(f"Hör mal {SHORT}", fetcher)
    assert parsed == ParsedLink(
        ShareLinkRef("spotify", "track", SP_TRACK), f"{SP}/track/{SP_TRACK}"
    )
    # The target page itself is never fetched.
    assert [(r.method, r.url) for r in web.request_history] == [("HEAD", SHORT)]
    assert web.last_request.headers["User-Agent"].startswith("python-requests/")


def test_spotify_short_link_via_branch_page(web, fetcher):
    web.head(SHORT, status_code=307, headers={"Location": APP_LINK})
    web.head(APP_LINK, status_code=200)
    web.get(SHORT, status_code=307, headers={"Location": APP_LINK})
    web.get(APP_LINK, content=BRANCH_PAGE, headers={"Content-Type": "text/html"})
    parsed = resolve(SHORT, fetcher)
    assert parsed.ref == ShareLinkRef("spotify", "playlist", SP_PLAYLIST)
    assert parsed.canonical_url == f"{SP}/playlist/{SP_PLAYLIST}"


@pytest.mark.parametrize(
    "fragment",
    [
        f"https://open.spotify.com/album/{SP_ALBUM}".encode(),
        f"open.spotify.com%2Fintl-de%2Falbum%2F{SP_ALBUM}%3Fsi%3D1".encode(),
        f"https://open.spotify.com/intl-de/album/{SP_ALBUM}".encode(),
    ],
)
def test_spotify_page_scan_formats(web, fetcher, fragment):
    web.head(SHORT, status_code=405)
    web.get(SHORT, content=b"<html><body><a href='" + fragment + b"'>Open</a></body></html>")
    assert resolve(SHORT, fetcher).ref == ShareLinkRef("spotify", "album", SP_ALBUM)


def test_spotify_page_scan_needs_a_full_id(web, fetcher):
    web.head(SHORT, status_code=405)
    web.get(SHORT, content=f"open.spotify.com/album/{SP_ALBUM}0".encode())
    with pytest.raises(LinkError) as info:
        resolve(SHORT, fetcher)
    assert info.value.code == UNRESOLVABLE


def test_dead_spotify_short_link(web, fetcher):
    web.head(SHORT, status_code=200)
    web.get(SHORT, content=b"<html><head><title>Spotify</title></head><body></body></html>")
    with pytest.raises(LinkError) as info:
        resolve(SHORT, fetcher)
    assert info.value.code == UNRESOLVABLE


def test_spotify_error_page_is_not_scanned(web, fetcher):
    web.head(SHORT, status_code=404)
    web.get(SHORT, status_code=404, content=f"{SP}/album/{SP_ALBUM}".encode())
    with pytest.raises(LinkError) as info:
        resolve(SHORT, fetcher)
    assert info.value.code == UNRESOLVABLE


def test_spotify_head_failure_falls_back_to_get(web, fetcher):
    web.head(SHORT, exc=requests.exceptions.ConnectTimeout)
    web.get(SHORT, status_code=307, headers={"Location": f"{SP}/album/{SP_ALBUM}?si=1"})
    assert resolve(SHORT, fetcher).ref.kind == "album"


def test_spotify_short_link_to_an_artist_is_unsupported(web, fetcher):
    web.head(SHORT, status_code=307, headers={"Location": f"{SP}/artist/{SP_ARTIST}?si=1"})
    with pytest.raises(LinkError) as info:
        resolve(SHORT, fetcher)
    assert (info.value.code, info.value.kind) == (UNSUPPORTED, "artist")
    assert web.call_count == 1


def test_short_link_to_an_app_scheme_is_unresolvable(web, fetcher):
    web.head(SHORT, status_code=307, headers={"Location": "intent://open/#Intent;end"})
    web.get(SHORT, status_code=307, headers={"Location": "intent://open/#Intent;end"})
    with pytest.raises(LinkError) as info:
        resolve(SHORT, fetcher)
    assert info.value.code == UNRESOLVABLE


def test_short_link_resolved_to_a_spotify_uri(web, fetcher):
    web.head(SHORT, status_code=307, headers={"Location": f"spotify:episode:{SP_EPISODE}"})
    assert resolve(SHORT, fetcher).ref == ShareLinkRef("spotify", "episode", SP_EPISODE)


def deezer_dest(target):
    return "https://link.deezer.com/?awf=1&dest=" + quote(target, safe="") + "&gwf=1&iwf=1"


def test_deezer_short_link_uses_the_dest_parameter(web, fetcher):
    short = "https://link.deezer.com/s/Ex4mpleCode"
    target = f"https://www.deezer.com/album/{DZ_ALBUM}?host=0&utm_campaign=clipboard-generic"
    web.get(short, status_code=301, headers={"Location": deezer_dest(target)})
    parsed = resolve(short, fetcher)
    assert parsed == ParsedLink(
        ShareLinkRef("deezer", "album", DZ_ALBUM), f"https://www.deezer.com/album/{DZ_ALBUM}"
    )
    assert web.call_count == 1


def test_pasted_deezer_dest_link_is_resolved_offline(web, fetcher):
    pasted = deezer_dest(f"https://www.deezer.com/track/{DZ_TRACK}")
    assert resolve(pasted, fetcher).ref == ShareLinkRef("deezer", "track", DZ_TRACK)
    assert web.call_count == 0


def test_deezer_dest_to_an_artist_is_unsupported(web, fetcher):
    short = "https://link.deezer.com/s/Ex4mpleCode"
    web.get(
        short, status_code=301, headers={"Location": deezer_dest("https://www.deezer.com/artist/1")}
    )
    with pytest.raises(LinkError) as info:
        resolve(short, fetcher)
    assert (info.value.code, info.value.kind) == (UNSUPPORTED, "artist")


def test_deezer_dest_must_be_a_web_link(web, fetcher):
    pasted = "https://link.deezer.com/?dest=ftp%3A%2F%2Fwww.deezer.com%2Falbum%2F1"
    web.get(pasted, status_code=404)
    with pytest.raises(LinkError) as info:
        resolve(pasted, fetcher)
    assert info.value.code == UNRESOLVABLE
    assert web.call_count == 1


def test_legacy_deezer_page_link(web, fetcher):
    short = "https://deezer.page.link/Ex4mple"
    target = f"https://www.deezer.com/playlist/{DZ_PLAYLIST}?utm_source=deezer"
    web.get(short, status_code=302, headers={"Location": target})
    assert resolve(short, fetcher).ref == ShareLinkRef("deezer", "playlist", DZ_PLAYLIST)


def test_dead_deezer_short_link(web, fetcher):
    short = "https://link.deezer.com/s/Ex4mpleDead"
    web.get(short, status_code=301, headers={"Location": "https://www.deezer.com/deezer-links-404"})
    web.get("https://www.deezer.com/deezer-links-404", status_code=404, content=b"gone")
    with pytest.raises(LinkError) as info:
        resolve(short, fetcher)
    assert info.value.code == UNRESOLVABLE


def test_tidal_short_link(web, fetcher):
    web.get(
        "https://tidal.link/Ex4mple",
        status_code=302,
        headers={"Location": f"https://tidal.com/browse/playlist/{TD_PLAYLIST}"},
    )
    parsed = resolve("http://tidal.link/Ex4mple", fetcher)
    assert parsed.canonical_url == f"https://tidal.com/playlist/{TD_PLAYLIST}"


def test_short_link_leaving_the_allowlist_is_unresolvable(web, fetcher):
    web.get(
        "https://tidal.link/Unkn0wn",
        status_code=301,
        headers={"Location": "https://bitly.example.net/not-found"},
    )
    with pytest.raises(LinkError) as info:
        resolve("https://tidal.link/Unkn0wn", fetcher)
    assert info.value.code == UNRESOLVABLE
    assert "host_not_allowed" in str(info.value)
    assert web.call_count == 1


def test_short_link_ending_on_a_page_is_unresolvable(web, fetcher):
    web.get("https://tidal.link/Ex4mple", content=b"<html>TIDAL</html>")
    with pytest.raises(LinkError) as info:
        resolve("https://tidal.link/Ex4mple", fetcher)
    assert info.value.code == UNRESOLVABLE


def test_short_link_redirect_loop_is_unresolvable(web, fetcher):
    web.get("https://tidal.link/Loop", status_code=302, headers={"Location": "/Loop"})
    with pytest.raises(LinkError) as info:
        resolve("https://tidal.link/Loop", fetcher)
    assert info.value.code == UNRESOLVABLE


def test_apple_short_link_through_itunes(web, fetcher):
    web.get(
        "https://apple.co/Ex4mple",
        status_code=301,
        headers={"Location": f"https://itunes.apple.com/de/album/id{AP_ALBUM}?i={AP_SONG}"},
    )
    web.get(
        f"https://itunes.apple.com/de/album/id{AP_ALBUM}?i={AP_SONG}",
        status_code=301,
        headers={"Location": f"{AP}/de/album/beispiel/{AP_ALBUM}?i={AP_SONG}&uo=4"},
    )
    parsed = resolve("https://apple.co/Ex4mple", fetcher)
    assert parsed.ref == ShareLinkRef("apple_music", "song", AP_SONG)
    assert parsed.canonical_url == f"{AP}/de/album/beispiel/{AP_ALBUM}?i={AP_SONG}"
    assert web.call_count == 2


def test_geo_apple_link_follows_the_storefront_redirect(web, fetcher):
    geo = f"https://geo.music.apple.com/us/album/_/{AP_ALBUM}"
    web.get(geo, status_code=301, headers={"Location": f"{AP}/de/album/beispiel/{AP_SONG}"})
    assert resolve(geo, fetcher).ref == ShareLinkRef("apple_music", "album", AP_SONG)
