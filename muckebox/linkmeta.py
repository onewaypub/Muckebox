# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share links: recognise them, resolve short links, fetch title and cover.

Parents paste share links from Spotify, Apple Music, TIDAL or Deezer into
the admin page, often with some text from the share sheet around them.

* :func:`parse` turns such text into a :class:`ParsedLink` (service, kind,
  ID and a canonical URL) without any network access.
* :func:`resolve` does the same, but first follows short links such as
  spotify.link, link.deezer.com, tidal.link or apple.co.
* :func:`metadata` finds a title and a cover image URL without API keys
  (oEmbed endpoints and ``og:`` tags), and :func:`fetch_image` downloads the
  cover.

All network access goes through :class:`muckebox.netfetch.Fetcher`, with a
host allowlist per purpose (:data:`RESOLVE_HOSTS`, :data:`SERVICE_HOSTS`,
:data:`IMAGE_HOSTS`). Error codes are i18n keys (``error.<code>``).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from .netfetch import Fetcher, FetchError, FetchResult, host_allowed
from .sonos.model import ShareLinkRef
from .sonos.sharelink import KINDS

UNSUPPORTED = "sharelink_unsupported"
UNRESOLVABLE = "sharelink_unresolvable"

#: Pages and oEmbed endpoints of each service (subdomains included).
SERVICE_HOSTS: dict[str, tuple[str, ...]] = {
    "spotify": ("spotify.com",),
    "apple_music": ("apple.com",),
    "tidal": ("tidal.com",),
    "deezer": ("deezer.com",),
}
#: Cover image CDNs of each service (subdomains included).
IMAGE_HOSTS: dict[str, tuple[str, ...]] = {
    "spotify": ("scdn.co", "spotifycdn.com"),
    "apple_music": ("mzstatic.com",),
    "tidal": ("resources.tidal.com",),
    "deezer": ("dzcdn.net",),
}
ALL_IMAGE_HOSTS: tuple[str, ...] = tuple(host for hosts in IMAGE_HOSTS.values() for host in hosts)
#: Hosts whose links only redirect to the real item, and their service.
SHORT_LINK_HOSTS: dict[str, str] = {
    "spotify.link": "spotify",
    "spoti.fi": "spotify",
    "spotify.app.link": "spotify",
    "link.deezer.com": "deezer",
    "deezer.page.link": "deezer",
    "tidal.link": "tidal",
    "apple.co": "apple_music",
    "itunes.apple.com": "apple_music",
    # Remaps catalogue IDs to the visitor's storefront when it redirects.
    "geo.music.apple.com": "apple_music",
}
#: Hosts a short-link resolution may visit: the short links and their targets.
RESOLVE_HOSTS: tuple[str, ...] = (
    *SHORT_LINK_HOSTS,
    *(host for hosts in SERVICE_HOSTS.values() for host in hosts),
)

HTML_MAX = 1024 * 1024
JSON_MAX = 256 * 1024
IMAGE_MAX = 10 * 1024 * 1024
SHORT_LINK_MAX = 1024 * 1024

SPOTIFY_OEMBED = "https://open.spotify.com/oembed?url="
APPLE_OEMBED = "https://music.apple.com/api/oembed?url="
DEEZER_OEMBED = "https://api.deezer.com/oembed?url="

COVER_SIZE = 600

# Item kinds each service has but Muckebox cannot play. Links to them are
# "unsupported" (add them as a Sonos favorite instead), not "unresolvable".
_UNSUPPORTED_KINDS = {
    "spotify": frozenset(
        ["artist", "user", "audiobook", "chapter", "collection", "genre", "concert", "local"]
    ),
    "apple_music": frozenset(
        [
            "artist",
            "station",
            "curator",
            "music-video",
            "library",
            "radio",
            "room",
            "post",
            "search",
            "browse",
            "show",
            "episode",
            "audiobook",
        ]
    ),
    "tidal": frozenset(["artist", "mix", "video", "profile", "search"]),
    "deezer": frozenset(
        [
            "artist",
            "show",
            "episode",
            "podcast",
            "profile",
            "radio",
            "channels",
            "search",
            "audiobook",
        ]
    ),
}

_SPOTIFY_HOSTS = frozenset({"open.spotify.com", "play.spotify.com"})
_APPLE_HOSTS = frozenset({"music.apple.com", "embed.music.apple.com"})
_TIDAL_HOSTS = frozenset({"tidal.com", "www.tidal.com", "listen.tidal.com"})
_DEEZER_HOSTS = frozenset({"deezer.com", "www.deezer.com"})

_URL_RE = re.compile(r"https?://[^\s<>\"'`«»“”„]+", re.IGNORECASE)
_SPOTIFY_URI_RE = re.compile(r"\bspotify:[A-Za-z0-9:._%-]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\""
_SPOTIFY_ID = re.compile(r"[A-Za-z0-9]{22}")
_DIGITS = re.compile(r"[0-9]+")
_APPLE_PLAYLIST_ID = re.compile(r"pl\.[-A-Za-z0-9]+")
_TIDAL_PLAYLIST_ID = re.compile(r"[-A-Za-z0-9]+")
_STOREFRONT = re.compile(r"[a-z]{2}", re.IGNORECASE)
_DEEZER_LOCALE = re.compile(r"[a-z]{2}(?:-[a-z]{2})?", re.IGNORECASE)
# open.spotify.com/<kind>/<id> as it appears in HTML, JS strings or query values.
_SEP = rb"(?:/|\\/|%2F)"
_SPOTIFY_IN_PAGE = re.compile(
    rb"open\.spotify\.com" + _SEP + rb"(?:intl-[a-z-]+" + _SEP + rb")?"
    rb"(album|playlist|track|episode|show)" + _SEP + rb"([A-Za-z0-9]{22})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


class LinkError(Exception):
    """A pasted link cannot be used.

    ``code`` is ``sharelink_unsupported`` (not a link Muckebox can play) or
    ``sharelink_unresolvable`` (a short link that could not be followed).
    ``kind`` names a recognised but unsupported item kind, e.g. ``"artist"``.
    """

    def __init__(self, code: str = UNSUPPORTED, message: str = "", *, kind: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.kind = kind


class ShortLinkError(LinkError):
    """:func:`parse` found a short link; :func:`resolve` can follow it.

    ``url`` is the short link, upgraded to https.
    """

    def __init__(self, url: str) -> None:
        super().__init__(UNRESOLVABLE, f"short link must be resolved first: {url}")
        self.url = url


@dataclass(frozen=True)
class ParsedLink:
    ref: ShareLinkRef
    canonical_url: str


@dataclass(frozen=True)
class LinkMeta:
    title: str | None
    image_url: str | None


# -- parsing (no network) ------------------------------------------------------


_STOREFRONT_RE = re.compile(r"[a-z]{2}")


def parse(text: str) -> ParsedLink:
    """Recognise the first share link in ``text``; raise :class:`LinkError`.

    Short links raise :class:`ShortLinkError` (code
    ``sharelink_unresolvable``); use :func:`resolve` to follow them.
    """
    return _parse_link(_extract(text))


def _extract(text: str) -> str:
    matches = [m for m in (_URL_RE.search(text), _SPOTIFY_URI_RE.search(text)) if m]
    if not matches:
        raise LinkError(UNSUPPORTED, "no link found")
    first = min(matches, key=lambda match: match.start())
    return first.group(0).rstrip(_TRAILING)


def _parse_link(link: str) -> ParsedLink:
    if link[:8].lower() == "spotify:":
        return _parse_spotify_uri(link)
    try:
        parts = urlsplit(link)
        host = (parts.hostname or "").rstrip(".")
    except ValueError as exc:
        raise LinkError(UNSUPPORTED, "not a valid URL") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise LinkError(UNSUPPORTED, "not a web link")
    segments = [segment for segment in parts.path.split("/") if segment]
    if host == "geo.music.apple.com" and segments and _STOREFRONT_RE.fullmatch(segments[0]):
        # Apple's badge links name the storefront. Following their redirect
        # would let Apple pick a storefront from the server's IP address and
        # swap in that storefront's catalogue IDs, so parse them directly.
        return _parse_apple(segments, parts.query)
    if host in SHORT_LINK_HOSTS:
        raise ShortLinkError(urlunsplit(("https", host, parts.path, parts.query, "")))
    if host in _SPOTIFY_HOSTS:
        return _parse_spotify_path(segments)
    if host in _APPLE_HOSTS:
        return _parse_apple(segments, parts.query)
    if host in _TIDAL_HOSTS:
        return _parse_tidal(segments)
    if host in _DEEZER_HOSTS:
        return _parse_deezer(segments)
    raise LinkError(UNSUPPORTED, f"unknown host: {host}")


def _unsupported(service: str, kind: str) -> LinkError:
    known = kind if kind in _UNSUPPORTED_KINDS[service] else None
    return LinkError(UNSUPPORTED, f"{service} link of kind {kind!r} is not supported", kind=known)


def _kind_and_id(service: str, segments: list[str]) -> tuple[str, str]:
    if not segments:
        raise LinkError(UNSUPPORTED, f"{service} link without item")
    kind = segments[0].lower()
    if kind not in KINDS[service] or len(segments) < 2:
        raise _unsupported(service, kind)
    return kind, segments[1]


def _parse_spotify_uri(uri: str) -> ParsedLink:
    parts = uri.split(":")[1:]
    if len(parts) >= 4 and parts[0].lower() == "user" and parts[2].lower() == "playlist":
        parts = parts[2:]  # legacy spotify:user:<name>:playlist:<id>
    return _spotify_link(*_kind_and_id("spotify", parts))


def _parse_spotify_path(segments: list[str]) -> ParsedLink:
    if segments and segments[0].lower().startswith("intl-"):
        segments = segments[1:]
    if segments and segments[0].lower() == "embed":
        segments = segments[1:]
    if len(segments) >= 4 and segments[0].lower() == "user" and segments[2].lower() == "playlist":
        segments = segments[2:]  # legacy /user/<name>/playlist/<id>
    return _spotify_link(*_kind_and_id("spotify", segments))


def _spotify_link(kind: str, item_id: str) -> ParsedLink:
    if not _SPOTIFY_ID.fullmatch(item_id):
        raise LinkError(UNSUPPORTED, "invalid Spotify ID")
    return ParsedLink(
        ShareLinkRef("spotify", kind, item_id), f"https://open.spotify.com/{kind}/{item_id}"
    )


def _parse_apple(segments: list[str], query: str) -> ParsedLink:
    storefront = "us"
    if segments and _STOREFRONT.fullmatch(segments[0]):
        storefront = segments[0].lower()
        segments = segments[1:]
    kind = segments[0].lower() if segments else ""
    song = parse_qs(query).get("i") if kind == "album" else None
    if song is not None:
        # An album link with ?i=<song> (anywhere in the query) is that song.
        album_link = _apple_link(storefront, segments, _DIGITS)
        if not _DIGITS.fullmatch(song[0]):
            raise LinkError(UNSUPPORTED, "invalid Apple Music song ID")
        return ParsedLink(
            ShareLinkRef("apple_music", "song", song[0]),
            f"{album_link.canonical_url}?i={song[0]}",
        )
    pattern = _APPLE_PLAYLIST_ID if kind == "playlist" else _DIGITS
    return _apple_link(storefront, segments, pattern)


def _apple_link(storefront: str, segments: list[str], pattern: re.Pattern[str]) -> ParsedLink:
    kind, _ = _kind_and_id("apple_music", segments)
    rest = segments[1:]
    item_id = rest[-1]
    if len(rest) > 2 or not pattern.fullmatch(item_id):
        raise LinkError(UNSUPPORTED, "invalid Apple Music link")
    path = f"/{storefront}/{kind}/"
    if len(rest) == 2:
        path += quote(unquote(rest[0]), safe="-._~") + "/"
    return ParsedLink(
        ShareLinkRef("apple_music", kind, item_id), f"https://music.apple.com{path}{item_id}"
    )


def _parse_tidal(segments: list[str]) -> ParsedLink:
    if segments and segments[0].lower() == "browse":
        segments = segments[1:]
    if len(segments) >= 4 and segments[0].lower() == "album" and segments[2].lower() == "track":
        segments = segments[2:]  # /album/<id>/track/<id> is the track
    kind, item_id = _kind_and_id("tidal", segments)
    pattern = _TIDAL_PLAYLIST_ID if kind == "playlist" else _DIGITS
    if not pattern.fullmatch(item_id):
        raise LinkError(UNSUPPORTED, "invalid TIDAL ID")
    return ParsedLink(ShareLinkRef("tidal", kind, item_id), f"https://tidal.com/{kind}/{item_id}")


def _parse_deezer(segments: list[str]) -> ParsedLink:
    if segments and _DEEZER_LOCALE.fullmatch(segments[0]):
        segments = segments[1:]
    kind, item_id = _kind_and_id("deezer", segments)
    if not _DIGITS.fullmatch(item_id):
        raise LinkError(UNSUPPORTED, "invalid Deezer ID")
    return ParsedLink(
        ShareLinkRef("deezer", kind, item_id), f"https://www.deezer.com/{kind}/{item_id}"
    )


# -- short links ------------------------------------------------------------------


def resolve(text: str, fetcher: Fetcher) -> ParsedLink:
    """Like :func:`parse`, but follow short links over the network first.

    Redirects are followed only until they reach a recognisable link, which
    is never fetched itself. A Spotify short link that ends on an HTML page
    (Branch.io) is scanned for the open.spotify.com link. Failures raise
    :class:`LinkError` with ``sharelink_unresolvable``.
    """
    try:
        return parse(text)
    except ShortLinkError as short:
        start = short.url
    found = _target(start)
    if found is not None:
        return _outcome(found)
    service = SHORT_LINK_HOSTS[urlsplit(start).hostname or ""]
    if service == "spotify":
        # Branch.io redirects HEAD requests of non-browser clients directly.
        try:
            result, found = _follow(start, fetcher, "HEAD")
        except FetchError:
            found = None
        if found is not None:
            return _outcome(found)
    try:
        result, found = _follow(start, fetcher, "GET")
    except FetchError as exc:
        raise LinkError(UNRESOLVABLE, f"cannot follow short link ({exc.code})") from exc
    if found is not None:
        return _outcome(found)
    if service == "spotify" and result.status == 200:
        match = _SPOTIFY_IN_PAGE.search(result.body)
        if match:
            return _spotify_link(match[1].decode().lower(), match[2].decode())
    raise LinkError(UNRESOLVABLE, "short link does not lead to a supported item")


def _target(url: str) -> ParsedLink | LinkError | None:
    """What a redirect target means: a link, an unsupported item, or keep going."""
    try:
        return _parse_link(url)
    except ShortLinkError as short:
        dest = _deezer_dest(short.url)
        return _target(dest) if dest else None
    except LinkError as exc:
        return exc if exc.kind else None


def _deezer_dest(url: str) -> str | None:
    """link.deezer.com/?dest=https://www.deezer.com/... (Deezer's own redirect form).

    Deezer follows ``dest`` only on the root path and only to www.deezer.com;
    on /s/<code> links it ignores ``dest``, so Muckebox must too.
    """
    parts = urlsplit(url)
    if parts.hostname != "link.deezer.com" or parts.path.strip("/"):
        return None
    dest = parse_qs(parts.query).get("dest", [""])[0]
    target = urlsplit(dest)
    if target.scheme.lower() != "https" or target.hostname != "www.deezer.com":
        return None
    return dest


def _outcome(found: ParsedLink | LinkError) -> ParsedLink:
    if isinstance(found, LinkError):
        raise found
    return found


class _StopAtLink:
    def __init__(self) -> None:
        self.found: ParsedLink | LinkError | None = None

    def __call__(self, target: str) -> bool:
        self.found = _target(target)
        return self.found is not None


def _follow(
    url: str, fetcher: Fetcher, method: str
) -> tuple[FetchResult, ParsedLink | LinkError | None]:
    stop = _StopAtLink()
    result = fetcher.get(
        url,
        method=method,
        max_bytes=SHORT_LINK_MAX,
        accept="text/html,*/*;q=0.8",
        allow=RESOLVE_HOSTS,
        stop_redirect=stop,
    )
    return result, stop.found if result.location else None


# -- metadata ---------------------------------------------------------------------


def metadata(link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    """Return title and cover image URL of ``link``; ``None`` where unknown.

    This is best effort: network and parse failures give ``None`` values,
    never an exception. Titles may be longer than a tile title may be.
    """
    strategy = _STRATEGIES.get(link.ref.service)
    if strategy is None:
        return LinkMeta(None, None)
    return strategy(link, fetcher)


# Spotify image IDs start with a size prefix; these map 300 px to 640 px.
_SPOTIFY_LARGE = {
    "ab67706f00000002": "ab67706f00000003",  # editorial playlist cover
    "ab67706c0000da84": "ab67706c0000bebb",  # user playlist cover
    "ab67616d00001e02": "ab67616d0000b273",  # album art
}
_SPOTIFY_IMAGE_ID_RE = re.compile(r"/image/([0-9a-f]{16})([0-9a-f]{24})$")


def _spotify_large(url: str) -> str | None:
    parts = urlsplit(url)
    if not host_allowed(parts.hostname or "", IMAGE_HOSTS["spotify"]):
        return None
    match = _SPOTIFY_IMAGE_ID_RE.search(parts.path)
    if not match or match[1] not in _SPOTIFY_LARGE:
        return None
    path = parts.path[: match.start()] + "/image/" + _SPOTIFY_LARGE[match[1]] + match[2]
    return urlunsplit(parts._replace(path=path))


def fetch_image(url: str, fetcher: Fetcher) -> bytes:
    """Download a cover image from one of the :data:`IMAGE_HOSTS`.

    For Spotify's 300 px images the 640 px variant is tried first. Raises
    :class:`~muckebox.netfetch.FetchError` (``bad_status`` for a response
    other than 200).
    """
    large = _spotify_large(url)
    if large is not None:
        try:
            return _get_image(large, fetcher)
        except FetchError:
            pass
    return _get_image(url, fetcher)


def _get_image(url: str, fetcher: Fetcher) -> bytes:
    result = fetcher.get(
        url, max_bytes=IMAGE_MAX, accept="image/jpeg,image/png,image/*;q=0.8", allow=ALL_IMAGE_HOSTS
    )
    if result.status != 200:
        raise FetchError("bad_status", f"image request answered {result.status}")
    return result.body


def _spotify_meta(link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    page = _page_meta(link, fetcher, titles=("og:title", "twitter:title"))
    if link.ref.kind in ("show", "episode"):
        # oEmbed describes a show's latest episode, not the show.
        return page
    embed = _oembed(SPOTIFY_OEMBED, link, fetcher)
    # og:image is 640 px, the oEmbed thumbnail only 300 px.
    return LinkMeta(embed.title or page.title, page.image_url or embed.image_url)


def _apple_meta(link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    embed = LinkMeta(None, None)
    if link.ref.kind != "song":  # oEmbed describes the album for song links
        embed = _oembed(APPLE_OEMBED, link, fetcher)
        if embed.title and embed.image_url:
            return embed
    page = _page_meta(
        link,
        fetcher,
        titles=("apple:title", "og:title", "twitter:title"),
        images=("twitter:image", "og:image"),
    )
    return LinkMeta(embed.title or page.title, embed.image_url or page.image_url)


def _tidal_meta(link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    page = _fetch_page(link.canonical_url, fetcher, "tidal")
    if page is None:
        return LinkMeta(None, None)
    tags = _page_tags(page, "tidal", ("og:title", "twitter:title"), ("og:image", "twitter:image"))
    if tags is None:
        return LinkMeta(None, None)
    name, image = _json_ld_item(page.json_ld)
    return LinkMeta(
        _clean_title(name, "tidal", decorated=False) or tags.title,
        _image_url(image, "tidal", page.url) or tags.image_url,
    )


def _deezer_meta(link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    embed = _oembed(DEEZER_OEMBED, link, fetcher)
    if embed.title and embed.image_url:
        return embed
    page = _page_meta(link, fetcher, titles=("og:title", "twitter:title"))
    return LinkMeta(embed.title or page.title, embed.image_url or page.image_url)


_STRATEGIES: dict[str, Callable[[ParsedLink, Fetcher], LinkMeta]] = {
    "spotify": _spotify_meta,
    "apple_music": _apple_meta,
    "tidal": _tidal_meta,
    "deezer": _deezer_meta,
}


def _oembed(endpoint: str, link: ParsedLink, fetcher: Fetcher) -> LinkMeta:
    service = link.ref.service
    url = endpoint + quote(link.canonical_url, safe="")
    try:
        result = fetcher.get(
            url, max_bytes=JSON_MAX, accept="application/json", allow=SERVICE_HOSTS[service]
        )
        if result.status != 200:
            return LinkMeta(None, None)
        data = json.loads(result.body.decode("utf-8"))
    except (FetchError, ValueError, RecursionError):
        return LinkMeta(None, None)
    if not isinstance(data, dict) or "error" in data:
        return LinkMeta(None, None)
    return LinkMeta(
        _clean_title(data.get("title"), service, decorated=False),
        _image_url(data.get("thumbnail_url"), service, result.url),
    )


# -- HTML pages -------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    url: str
    meta: dict[str, str]
    title: str | None
    json_ld: list[str]


class _HeadParser(HTMLParser):
    """Collects <meta> tags, the <title> and JSON-LD scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self.json_ld: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value for name, value in attrs if value is not None}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            if key and "content" in values:
                self.meta.setdefault(key, values["content"])
        elif tag == "title" and self.title is None:
            self._start("title")
        elif tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._start("ld")

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        text = "".join(self._buffer)
        if self._capture == "title" and tag == "title":
            self.title = text
            self._capture = None
        elif self._capture == "ld" and tag == "script":
            self.json_ld.append(text)
            self._capture = None

    def _start(self, what: str) -> None:
        self._capture = what
        self._buffer = []


def _fetch_page(url: str, fetcher: Fetcher, service: str) -> _Page | None:
    try:
        result = fetcher.get(
            url,
            max_bytes=HTML_MAX,
            accept="text/html",
            allow=SERVICE_HOSTS[service],
            stop_at=b"</head>",
            truncate=True,
        )
    except FetchError:
        return None
    if result.status != 200:
        return None
    parser = _HeadParser()
    # Apple and TIDAL send "text/html" without a charset; the pages are UTF-8.
    parser.feed(result.body.decode("utf-8", "replace"))
    parser.close()
    return _Page(result.url, parser.meta, parser.title, parser.json_ld)


def _page_meta(
    link: ParsedLink,
    fetcher: Fetcher,
    *,
    titles: tuple[str, ...],
    images: tuple[str, ...] = ("og:image", "twitter:image"),
) -> LinkMeta:
    page = _fetch_page(link.canonical_url, fetcher, link.ref.service)
    tags = _page_tags(page, link.ref.service, titles, images) if page else None
    return tags or LinkMeta(None, None)


def _page_tags(
    page: _Page, service: str, titles: tuple[str, ...], images: tuple[str, ...]
) -> LinkMeta | None:
    """Title and image from meta tags, or ``None`` for a generic page."""
    headline = page.meta.get("og:title") or page.title
    if headline and headline.strip() and _clean_title(headline, service, decorated=True) is None:
        return None  # e.g. "Spotify" for a dead link, "Not Found - TIDAL"
    title = next(
        (
            cleaned
            for key in titles
            if (cleaned := _clean_title(page.meta.get(key), service, decorated=True))
        ),
        None,
    )
    image = next(
        (url for key in images if (url := _image_url(page.meta.get(key), service, page.url))),
        None,
    )
    return LinkMeta(title or _clean_title(page.title, service, decorated=True), image)


def _json_ld_item(blocks: list[str]) -> tuple[str | None, str | None]:
    """Name and image of the first music item in JSON-LD blocks."""
    for block in blocks:
        try:
            data = json.loads(block)
        except (ValueError, RecursionError):
            continue
        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            items = [data, *data["@graph"]]
        for item in items:
            if isinstance(item, dict) and _is_music_item(item.get("@type")):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    return name, _json_ld_image(item.get("image"))
    return None, None


_MUSIC_TYPES = frozenset({"MusicAlbum", "MusicRecording", "MusicPlaylist"})


def _is_music_item(kind: object) -> bool:
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k in _MUSIC_TYPES for k in kinds)


def _json_ld_image(image: object) -> str | None:
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    return image if isinstance(image, str) else None


# -- titles and images ------------------------------------------------------------

_SUFFIX = re.compile(
    r"(?:\s*[|·–—-]\s*|\s+(?:on|bei|auf)\s+)"
    r"(?:podcast\s+(?:on|bei|auf)\s+)?(?:spotify|apple\s+music|tidal|deezer)\s*$",
    re.IGNORECASE,
)
# "Global Warming - Album by Pitbull" (Spotify album og:title).
_SPOTIFY_DECORATION = re.compile(
    r"\s+[–—-]\s+(?:album|single|ep|compilation|playlist|song(?:\s+and\s+lyrics)?"
    r"|podcast|episode|audiobook|hörbuch)\s+(?:by|von)\s+.+$",
    re.IGNORECASE,
)
# "„Black Velvet“ von Alannah Myles" (Apple Music og:title in German).
_APPLE_QUOTED = re.compile(r"^[„“«\"](.+?)[“”»\"]\s+(?:von|by)\s+\S.*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_GENERIC_TITLES = frozenset(
    {
        "spotify",
        "apple music",
        "tidal",
        "deezer",
        "not found",
        "page not found",
        "404",
        "404 not found",
        "error",
        "web player",
        "spotify web player",
        "spotify web player music for everyone",
        "apple music web player",
    }
)


def _is_generic(title: str) -> bool:
    return " ".join(re.sub(r"\W+", " ", title.casefold()).split()) in _GENERIC_TITLES


def _clean_title(value: object, service: str, *, decorated: bool) -> str | None:
    """Normalise a title; ``None`` if empty or a service placeholder.

    ``decorated`` titles come from HTML pages: the parser already unescaped
    them, and they may carry a service suffix such as " | Spotify". Titles
    from JSON are unescaped here and not trimmed.
    """
    if not isinstance(value, str):
        return None
    text = value if decorated else html.unescape(value)
    text = " ".join(_CONTROL.sub(" ", text).split())
    cleaned = text
    if decorated:
        cleaned = _SUFFIX.sub("", cleaned)
        if service == "spotify":
            cleaned = _SPOTIFY_DECORATION.sub("", cleaned)
        elif service == "apple_music" and (quoted := _APPLE_QUOTED.match(cleaned)):
            cleaned = quoted[1]
        cleaned = cleaned.strip()
    if not cleaned or _is_generic(text) or _is_generic(cleaned):
        return None
    return cleaned


def _image_url(value: object, service: str, base: str) -> str | None:
    """An absolute https image URL on the service's image hosts, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(urljoin(base, value.strip()))
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme.lower() == "http":
        parts = parts._replace(scheme="https")
    if parts.scheme.lower() != "https" or not host_allowed(host, IMAGE_HOSTS[service]):
        return None  # e.g. Apple's generic apple-music.png on music.apple.com
    url = urlunsplit(parts)
    return apple_artwork(url) if service == "apple_music" else url


_MZ_TEMPLATE = (("{w}", str(COVER_SIZE)), ("{h}", str(COVER_SIZE)), ("{c}", "bb"), ("{f}", "jpg"))
_MZ_SIZE = re.compile(
    r"/[0-9]+x[0-9]+([A-Za-z]{2}(?:\.[A-Za-z0-9]+)*)?(?:-[0-9]+)?\.(?:jpe?g|png|webp)$",
    re.IGNORECASE,
)


def apple_artwork(url: str, size: int = COVER_SIZE) -> str:
    """Ask Apple's image CDN (mzstatic) for a square ``size`` JPEG.

    The last path segment encodes the size: ``1200x630wp.jpg`` (padded to a
    wide image) becomes ``600x600bb.jpg``; other crop codes are kept. URLs
    that do not follow the pattern are returned unchanged.
    """
    for placeholder, value in _MZ_TEMPLATE:
        url = url.replace(placeholder, value)
    parts = urlsplit(url)
    match = _MZ_SIZE.search(parts.path)
    if not match:
        return url
    crop = match[1] or "bb"
    if crop.lower() == "wp":
        crop = "bb"
    path = f"{parts.path[: match.start()]}/{size}x{size}{crop}.jpg"
    return urlunsplit(parts._replace(path=path))
