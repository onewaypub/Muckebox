# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
from urllib.parse import parse_qs, quote, urlsplit

import pytest
import requests
import requests_mock

from muckebox import linkmeta
from muckebox.linkmeta import (
    APPLE_OEMBED,
    DEEZER_OEMBED,
    SPOTIFY_OEMBED,
    UNRESOLVABLE,
    UNSUPPORTED,
    LinkError,
    LinkMeta,
    ParsedLink,
    ShortLinkError,
    apple_artwork,
    fetch_image,
    metadata,
    parse,
    resolve,
)
from muckebox.netfetch import Fetcher, FetchError
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


# -- metadata: Spotify ---------------------------------------------------------------------


def oembed(endpoint, canonical):
    return endpoint + quote(canonical, safe="")


def page(*head, body=""):
    return ("<!DOCTYPE html><html><head>" + "".join(head) + "</head><body>" + body).encode()


def meta(key, value, attr="property"):
    return f'<meta {attr}="{key}" content="{value}">'


SP_ALBUM_LINK = link("spotify", "album", SP_ALBUM, f"{SP}/album/{SP_ALBUM}")
SP_OG_IMAGE = "https://i.scdn.co/image/ab67616d0000b273example"
SP_THUMB = "https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e02example"
SP_ALBUM_PAGE = page(
    "<title>Sommerlieder - Album by Beispiel-Band | Spotify</title>",
    meta("og:site_name", "Spotify"),
    meta("og:title", "Sommerlieder &amp; Co - Album by Beispiel-Band | Spotify"),
    meta("og:image", SP_OG_IMAGE),
    meta("og:image:width", "640"),
    body="<p>" + "x" * 5000 + "</p>",
)
SP_ALBUM_OEMBED = {
    "html": "<iframe></iframe>",
    "iframe_url": f"{SP}/embed/album/{SP_ALBUM}",
    "provider_name": "Spotify",
    "type": "rich",
    "title": "Sommerlieder &amp; Co",
    "thumbnail_url": SP_THUMB,
    "thumbnail_width": 300,
}


def test_spotify_album_title_from_oembed_and_large_cover_from_page(web, fetcher):
    web.get(oembed(SPOTIFY_OEMBED, SP_ALBUM_LINK.canonical_url), json=SP_ALBUM_OEMBED)
    web.get(
        SP_ALBUM_LINK.canonical_url,
        content=SP_ALBUM_PAGE,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert metadata(SP_ALBUM_LINK, fetcher) == LinkMeta("Sommerlieder & Co", SP_OG_IMAGE)
    embed_request = next(r for r in web.request_history if r.path == "/oembed")
    assert parse_qs(urlsplit(embed_request.url).query) == {"url": [SP_ALBUM_LINK.canonical_url]}
    assert embed_request.headers["Accept"] == "application/json"


def test_spotify_falls_back_to_page_title(web, fetcher):
    web.get(oembed(SPOTIFY_OEMBED, SP_ALBUM_LINK.canonical_url), status_code=504)
    web.get(SP_ALBUM_LINK.canonical_url, content=SP_ALBUM_PAGE)
    assert metadata(SP_ALBUM_LINK, fetcher) == LinkMeta("Sommerlieder & Co", SP_OG_IMAGE)


def test_spotify_falls_back_to_oembed_thumbnail(web, fetcher):
    web.get(oembed(SPOTIFY_OEMBED, SP_ALBUM_LINK.canonical_url), json=SP_ALBUM_OEMBED)
    web.get(SP_ALBUM_LINK.canonical_url, exc=requests.exceptions.ConnectTimeout)
    assert metadata(SP_ALBUM_LINK, fetcher) == LinkMeta("Sommerlieder & Co", SP_THUMB)


def test_spotify_without_any_answer(web, fetcher):
    web.get(oembed(SPOTIFY_OEMBED, SP_ALBUM_LINK.canonical_url), exc=requests.exceptions.SSLError)
    web.get(SP_ALBUM_LINK.canonical_url, status_code=500)
    assert metadata(SP_ALBUM_LINK, fetcher) == LinkMeta(None, None)


@pytest.mark.parametrize(
    "head",
    [
        meta("og:title", "Spotify") + meta("og:image", "https://i.scdn.co/image/branch-generic"),
        "<title>Spotify – Web Player: Music for everyone</title>",
        meta("og:title", " | Spotify"),
    ],
)
def test_spotify_placeholder_pages_give_nothing(web, fetcher, head):
    web.get(oembed(SPOTIFY_OEMBED, SP_ALBUM_LINK.canonical_url), status_code=404)
    web.get(SP_ALBUM_LINK.canonical_url, content=page(head))
    assert metadata(SP_ALBUM_LINK, fetcher) == LinkMeta(None, None)


@pytest.mark.parametrize("kind", ["show", "episode"])
def test_spotify_shows_and_episodes_use_the_page_only(web, fetcher, kind):
    item = SP_SHOW if kind == "show" else SP_EPISODE
    target = link("spotify", kind, item, f"{SP}/{kind}/{item}")
    image = "https://i.scdn.co/image/ab6765630000ba8aexample"
    web.get(
        target.canonical_url,
        content=page(
            meta("og:title", "Die Hörspiel-Kiste"),
            meta("og:image", image),
        ),
    )
    assert metadata(target, fetcher) == LinkMeta("Die Hörspiel-Kiste", image)
    assert [r.path for r in web.request_history] == [f"/{kind}/{item.lower()}"]


# -- metadata: Apple Music ---------------------------------------------------------------

AP_ALBUM_LINK = link("apple_music", "album", AP_ALBUM, f"{AP}/de/album/beispiel/{AP_ALBUM}")
AP_ART = "https://is1-ssl.mzstatic.com/image/thumb/Music/v4/aa/bb/cc/Example.rgb.jpg"
AP_SONG_LINK = link(
    "apple_music", "song", AP_SONG, f"{AP}/de/album/beispiel/{AP_ALBUM}?i={AP_SONG}"
)
AP_SONG_PAGE = page(
    meta("apple:title", "Schöne Grüße aus Köln", attr="name"),
    meta("og:title", "„Schöne Grüße aus Köln“ von Beispiel-Band bei Apple Music"),
    meta("og:image", f"{AP_ART}/1200x630wp-60.jpg"),
    meta("twitter:image", f"{AP_ART}/600x600bf-60.jpg", attr="name"),
    '<meta charset="utf-8">',
)


def test_apple_album_from_oembed(web, fetcher):
    web.get(
        oembed(APPLE_OEMBED, AP_ALBUM_LINK.canonical_url),
        json={
            "version": "1.0",
            "type": "rich",
            "provider_name": "Apple Music",
            "title": "Weiße Weihnacht (Example's Version)",
            "thumbnail_url": f"{AP_ART}/300x300bb.jpg",
            "thumbnail_width": "300",
            "author_name": "Beispiel-Band",
        },
    )
    assert metadata(AP_ALBUM_LINK, fetcher) == LinkMeta(
        "Weiße Weihnacht (Example's Version)", f"{AP_ART}/600x600bb.jpg"
    )
    assert web.call_count == 1


def test_apple_song_page_without_charset_keeps_umlauts_and_quotes(web, fetcher):
    # requests would decode this as ISO-8859-1; linkmeta decodes UTF-8 itself.
    assert requests.utils.get_encoding_from_headers({"content-type": "text/html"}) == "ISO-8859-1"
    web.get(AP_SONG_LINK.canonical_url, content=AP_SONG_PAGE, headers={"Content-Type": "text/html"})
    assert metadata(AP_SONG_LINK, fetcher) == LinkMeta(
        "Schöne Grüße aus Köln", f"{AP_ART}/600x600bf.jpg"
    )
    # Songs skip oEmbed: it would describe the whole album.
    assert web.call_count == 1


def test_apple_song_falls_back_to_decorated_og_title(web, fetcher):
    web.get(
        AP_SONG_LINK.canonical_url,
        headers={"Content-Type": "text/html"},
        content=page(
            meta("og:title", "„Schöne Grüße aus Köln“ von Beispiel-Band bei Apple Music"),
            meta("og:image", f"{AP_ART}/1200x630wp-60.jpg"),
        ),
    )
    assert metadata(AP_SONG_LINK, fetcher) == LinkMeta(
        "Schöne Grüße aus Köln", f"{AP_ART}/600x600bb.jpg"
    )


def test_apple_album_falls_back_to_the_page(web, fetcher):
    web.get(oembed(APPLE_OEMBED, AP_ALBUM_LINK.canonical_url), status_code=404)
    web.get(AP_ALBUM_LINK.canonical_url, content=AP_SONG_PAGE)
    assert metadata(AP_ALBUM_LINK, fetcher) == LinkMeta(
        "Schöne Grüße aus Köln", f"{AP_ART}/600x600bf.jpg"
    )


def test_apple_oembed_title_with_page_cover(web, fetcher):
    web.get(oembed(APPLE_OEMBED, AP_ALBUM_LINK.canonical_url), json={"title": "Sommer"})
    web.get(AP_ALBUM_LINK.canonical_url, content=AP_SONG_PAGE)
    assert metadata(AP_ALBUM_LINK, fetcher) == LinkMeta("Sommer", f"{AP_ART}/600x600bf.jpg")


def test_apple_generic_page_gives_nothing(web, fetcher):
    web.get(
        AP_SONG_LINK.canonical_url,
        content=page(
            meta("og:title", "Apple Music"),
            meta("og:image", "https://music.apple.com/assets/meta/apple-music.png"),
        ),
    )
    assert metadata(AP_SONG_LINK, fetcher) == LinkMeta(None, None)


def test_apple_generic_image_is_ignored(web, fetcher):
    web.get(
        AP_SONG_LINK.canonical_url,
        content=page(
            meta("og:title", "Sommer on Apple Music"),
            meta("og:image", "https://music.apple.com/assets/meta/apple-music.png"),
        ),
    )
    assert metadata(AP_SONG_LINK, fetcher) == LinkMeta("Sommer", None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{AP_ART}/1200x630wp.jpg", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/1200x630wp-60.jpg", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/300x300bb.jpg", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/600x600bf-60.jpg", f"{AP_ART}/600x600bf.jpg"),
        (f"{AP_ART}/300x300SC.DN01.jpg?l=en-US", f"{AP_ART}/600x600SC.DN01.jpg?l=en-US"),
        (f"{AP_ART}/1200x600SC.DN01-60.jpg", f"{AP_ART}/600x600SC.DN01.jpg"),
        (f"{AP_ART}/1000x1000.png", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/316x316bb.webp", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/{{w}}x{{h}}{{c}}.{{f}}", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/{{w}}x{{h}}bb.{{f}}", f"{AP_ART}/600x600bb.jpg"),
        (f"{AP_ART}/artwork.jpg", f"{AP_ART}/artwork.jpg"),
        ("https://is1-ssl.mzstatic.com/", "https://is1-ssl.mzstatic.com/"),
    ],
)
def test_apple_artwork_size_rewrite(url, expected):
    assert apple_artwork(url) == expected


def test_apple_artwork_other_size():
    assert apple_artwork(f"{AP_ART}/300x300bb.jpg", size=240) == f"{AP_ART}/240x240bb.jpg"


# -- metadata: TIDAL ---------------------------------------------------------------------

TD_ALBUM_LINK = link("tidal", "album", TD_ALBUM, f"https://tidal.com/album/{TD_ALBUM}")
TD_IMAGE = "https://resources.tidal.com/images/aa11/bb22/cc33/dd44/640x640.jpg"
TD_LD_IMAGE = "https://resources.tidal.com/images/aa11/bb22/cc33/dd44/1280x1280.jpg"


def json_ld(data):
    return '<script type="application/ld+json">' + json.dumps(data) + "</script>"


def tidal_page(*extra):
    return page(
        meta("og:title", "Beispiel-Band - Grüße vom Mond"),
        meta("og:image", TD_IMAGE),
        "<title>Grüße vom Mond by Beispiel-Band on TIDAL</title>",
        *extra,
        '<meta charset="utf-8">',
    )


@pytest.mark.parametrize(
    "ld",
    [
        {"@type": "MusicAlbum", "name": "Grüße vom Mond", "image": TD_LD_IMAGE},
        [
            {"@type": "WebSite", "name": "TIDAL"},
            {"@type": ["MusicAlbum"], "name": "Grüße vom Mond", "image": [TD_LD_IMAGE]},
        ],
        {
            "@graph": [
                {"@type": "MusicAlbum", "name": "Grüße vom Mond", "image": {"url": TD_LD_IMAGE}}
            ]
        },
        {
            "@type": "MusicRecording",
            "name": "Grüße vom Mond",
            "image": {"@type": "ImageObject", "contentUrl": TD_LD_IMAGE},
        },
    ],
)
def test_tidal_prefers_json_ld(web, fetcher, ld):
    web.get(
        TD_ALBUM_LINK.canonical_url,
        headers={"Content-Type": "text/html"},
        content=tidal_page(json_ld(ld)),
    )
    assert metadata(TD_ALBUM_LINK, fetcher) == LinkMeta("Grüße vom Mond", TD_LD_IMAGE)


@pytest.mark.parametrize(
    "extra",
    [
        "",
        '<script type="application/ld+json">{not json</script>',
        json_ld({"@type": "MusicAlbum", "name": "  "}),
        json_ld({"@type": "MusicAlbum", "name": "Grüße vom Mond", "image": []}),
        json_ld("just a string"),
        '<script type="application/ld+json">' + "[" * 100_000 + "</script>",
        "<script>var x = 1;</script>",
    ],
)
def test_tidal_falls_back_to_og_tags(web, fetcher, extra):
    web.get(TD_ALBUM_LINK.canonical_url, content=tidal_page(extra))
    result = metadata(TD_ALBUM_LINK, fetcher)
    assert result.image_url == TD_IMAGE
    assert result.title in ("Beispiel-Band - Grüße vom Mond", "Grüße vom Mond")


def test_tidal_json_ld_after_the_head_is_not_read(web, fetcher):
    body = json_ld({"@type": "MusicAlbum", "name": "Im Body", "image": TD_LD_IMAGE})
    web.get(TD_ALBUM_LINK.canonical_url, content=page(meta("og:title", "Kopf"), body=body))
    assert metadata(TD_ALBUM_LINK, fetcher) == LinkMeta("Kopf", None)


def test_tidal_uses_the_title_tag_without_og_title(web, fetcher):
    web.get(
        TD_ALBUM_LINK.canonical_url,
        content=page(
            "<title>Grüße vom Mond on TIDAL</title>",
            meta("og:image", "https://tidal.com/img/FB_1200x627.png"),
        ),
    )
    assert metadata(TD_ALBUM_LINK, fetcher) == LinkMeta("Grüße vom Mond", None)


@pytest.mark.parametrize(("status", "title"), [(404, "Beispiel"), (200, "Not Found - TIDAL")])
def test_tidal_missing_items_give_nothing(web, fetcher, status, title):
    web.get(
        TD_ALBUM_LINK.canonical_url,
        status_code=status,
        content=page(
            meta("og:title", title),
            meta("og:image", TD_IMAGE),
        ),
    )
    assert metadata(TD_ALBUM_LINK, fetcher) == LinkMeta(None, None)


def test_tidal_network_failure_gives_nothing(web, fetcher):
    web.get(TD_ALBUM_LINK.canonical_url, exc=requests.exceptions.ConnectionError)
    assert metadata(TD_ALBUM_LINK, fetcher) == LinkMeta(None, None)


# -- metadata: Deezer --------------------------------------------------------------------

DZ_ALBUM_LINK = link("deezer", "album", DZ_ALBUM, f"https://www.deezer.com/album/{DZ_ALBUM}")
DZ_COVER = "https://cdn-images.dzcdn.net/images/cover/0123abcd/1000x1000.jpg"
DZ_OG_IMAGE = "https://e-cdns-images.dzcdn.net/images/cover/0123abcd/500x500.jpg"


def test_deezer_from_oembed(web, fetcher):
    web.get(
        oembed(DEEZER_OEMBED, DZ_ALBUM_LINK.canonical_url),
        json={
            "version": "1.0",
            "type": "rich",
            "entity": "album",
            "id": int(DZ_ALBUM),
            "title": "Bibi &amp; Tina: Folge 1",
            "author_name": "Beispiel",
            "thumbnail_url": DZ_COVER,
        },
    )
    assert metadata(DZ_ALBUM_LINK, fetcher) == LinkMeta("Bibi & Tina: Folge 1", DZ_COVER)
    assert web.last_request.hostname == "api.deezer.com"


@pytest.mark.parametrize(
    "answer",
    [
        {"status_code": 404, "json": {"error": {"type": "SimpleApiHttpException", "code": 404}}},
        {"status_code": 200, "json": {"error": {"type": "DataException", "code": 800}}},
        {"status_code": 200, "content": b"not json"},
        {"status_code": 200, "content": b"\xff\xfe"},
        {"status_code": 200, "json": ["a", "list"]},
        {"status_code": 200, "json": {"title": None, "thumbnail_url": 42}},
        {"exc": requests.exceptions.ReadTimeout},
    ],
)
def test_deezer_falls_back_to_the_page(web, fetcher, answer):
    web.get(oembed(DEEZER_OEMBED, DZ_ALBUM_LINK.canonical_url), **answer)
    web.get(
        DZ_ALBUM_LINK.canonical_url,
        content=page(
            meta("og:title", "Bibi &amp; Tina: Folge 1"),
            meta("og:image", DZ_OG_IMAGE),
        ),
    )
    assert metadata(DZ_ALBUM_LINK, fetcher) == LinkMeta("Bibi & Tina: Folge 1", DZ_OG_IMAGE)


def test_deezer_partial_oembed_is_completed_from_the_page(web, fetcher):
    web.get(oembed(DEEZER_OEMBED, DZ_ALBUM_LINK.canonical_url), json={"title": "Sommer"})
    web.get(DZ_ALBUM_LINK.canonical_url, content=page(meta("og:image", DZ_OG_IMAGE)))
    assert metadata(DZ_ALBUM_LINK, fetcher) == LinkMeta("Sommer", DZ_OG_IMAGE)


# -- metadata: general --------------------------------------------------------------------


def test_unknown_service_gives_nothing(web, fetcher):
    other = link("example", "album", "1", "https://www.example.com/album/1")
    assert metadata(other, fetcher) == LinkMeta(None, None)
    assert web.call_count == 0


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("//i.scdn.co/image/abc", "https://i.scdn.co/image/abc"),
        ("http://i.scdn.co/image/abc", "https://i.scdn.co/image/abc"),
        ("https://mosaic.scdn.co/640/abc", "https://mosaic.scdn.co/640/abc"),
        ("/image/abc", None),
        ("https://images.example.com/abc.jpg", None),
        ("javascript:alert(1)", None),
        ("data:image/png;base64,AAAA", None),
        ("https://[broken/abc", None),
        ("   ", None),
    ],
)
def test_cover_urls_must_be_https_on_the_image_hosts(web, fetcher, image, expected):
    target = link("spotify", "show", SP_SHOW, f"{SP}/show/{SP_SHOW}")
    web.get(target.canonical_url, content=page(meta("og:title", "Show"), meta("og:image", image)))
    assert metadata(target, fetcher) == LinkMeta("Show", expected)


def test_first_meta_tag_wins_and_attributes_are_parsed_leniently(web, fetcher):
    target = link("spotify", "show", SP_SHOW, f"{SP}/show/{SP_SHOW}")
    web.get(
        target.canonical_url,
        content=page(
            '<META PROPERTY="og:title">',
            '<meta property="og:title" content="Erste">',
            '<meta property="og:title" content="Zweite">',
            '<meta name="twitter:image" content="https://i.scdn.co/image/tw">',
            "<title>Titel</title><title>Noch ein Titel</title>",
        ),
    )
    assert metadata(target, fetcher) == LinkMeta("Erste", "https://i.scdn.co/image/tw")


def test_large_pages_are_truncated_not_rejected(web, fetcher):
    target = link("spotify", "show", SP_SHOW, f"{SP}/show/{SP_SHOW}")
    content = page(meta("og:title", "Groß"), "<style>" + "x" * (2 * 1024 * 1024) + "</style>")
    web.get(target.canonical_url, content=content)
    assert metadata(target, fetcher).title == "Groß"


@pytest.mark.parametrize(
    ("value", "service", "decorated", "expected"),
    [
        ("Kinderlieder | Spotify", "spotify", True, "Kinderlieder"),
        ("Die Hörspiel-Kiste | Podcast on Spotify", "spotify", True, "Die Hörspiel-Kiste"),
        ("Sommer – Album von Beispiel-Band | Spotify", "spotify", True, "Sommer"),
        ("Sommer - song and lyrics by Beispiel-Band | Spotify", "spotify", True, "Sommer"),
        ("Sommer - Single by Beispiel | Spotify", "spotify", True, "Sommer"),
        ("Sommer by Beispiel on Apple Music", "apple_music", True, "Sommer by Beispiel"),
        ("„Weiße Wolken“ von Beispiel bei Apple Music", "apple_music", True, "Weiße Wolken"),
        ("“Blue Sky” by Example on Apple Music", "apple_music", True, "Blue Sky"),
        ("Die Geschichte von „Bibi“", "apple_music", True, "Die Geschichte von „Bibi“"),
        ("Sommer - Apple Music", "apple_music", True, "Sommer"),
        ("Apple Music Web Player", "apple_music", True, None),
        ("Sommer on TIDAL", "tidal", True, "Sommer"),
        ("Not Found - TIDAL", "tidal", True, None),
        ("Sommer auf Deezer", "deezer", True, "Sommer"),
        ("Deezer", "deezer", True, None),
        ("Spotify", "spotify", True, None),
        ("404", "spotify", True, None),
        ("  Viel \t  Platz\n ", "spotify", True, "Viel Platz"),
        ("Null\x00Byte", "spotify", True, "Null Byte"),
        ("", "spotify", True, None),
        (None, "spotify", True, None),
        (42, "spotify", False, None),
        ("Bibi &amp; Tina", "spotify", False, "Bibi & Tina"),
        ("Best of | Spotify", "spotify", False, "Best of | Spotify"),
        ("Sommer - Album by Beispiel", "spotify", False, "Sommer - Album by Beispiel"),
    ],
)
def test_title_cleaning(value, service, decorated, expected):
    assert linkmeta._clean_title(value, service, decorated=decorated) == expected


# -- fetch_image -----------------------------------------------------------------------


def test_fetch_image(web, fetcher):
    web.get(SP_OG_IMAGE, content=b"\xff\xd8\xffjpeg", headers={"Content-Type": "image/jpeg"})
    assert fetch_image(SP_OG_IMAGE, fetcher) == b"\xff\xd8\xffjpeg"
    assert web.last_request.headers["Accept"].startswith("image/")


def test_fetch_image_follows_redirects_between_image_hosts(web, fetcher):
    web.get(SP_OG_IMAGE, status_code=302, headers={"Location": SP_THUMB})
    web.get(SP_THUMB, content=b"img")
    assert fetch_image(SP_OG_IMAGE, fetcher) == b"img"


def test_fetch_image_rejects_error_responses(web, fetcher):
    web.get(DZ_COVER, status_code=404)
    with pytest.raises(FetchError) as info:
        fetch_image(DZ_COVER, fetcher)
    assert info.value.code == "bad_status"


@pytest.mark.parametrize(
    "url",
    [
        "https://music.apple.com/assets/meta/apple-music.png",
        "https://open.spotify.com/image/abc",
        "http://i.scdn.co/image/abc",
    ],
)
def test_fetch_image_only_from_image_hosts(web, fetcher, url):
    with pytest.raises(FetchError):
        fetch_image(url, fetcher)
    assert web.call_count == 0


def test_fetch_image_size_limit(web, fetcher):
    web.get(TD_IMAGE, content=b"x", headers={"Content-Length": str(11 * 1024 * 1024)})
    with pytest.raises(FetchError) as info:
        fetch_image(TD_IMAGE, fetcher)
    assert info.value.code == "too_large"
