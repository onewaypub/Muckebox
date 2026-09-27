# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share links: recognise them and resolve short links.

Parents paste share links from Spotify, Apple Music, TIDAL or Deezer into
the admin page, often with some text from the share sheet around them.

* :func:`parse` turns such text into a :class:`ParsedLink` (service, kind,
  ID and a canonical URL) without any network access.
* :func:`resolve` does the same, but first follows short links such as
  spotify.link, link.deezer.com, tidal.link or apple.co.

All network access goes through :class:`muckebox.netfetch.Fetcher`, with
the host allowlist :data:`RESOLVE_HOSTS`. Error codes are i18n keys
(``error.<code>``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit

from .netfetch import Fetcher, FetchError, FetchResult
from .sonos.model import ShareLinkRef
from .sonos.sharelink import KINDS

UNSUPPORTED = "sharelink_unsupported"
UNRESOLVABLE = "sharelink_unresolvable"

#: Pages of each service (subdomains included).
SERVICE_HOSTS: dict[str, tuple[str, ...]] = {
    "spotify": ("spotify.com",),
    "apple_music": ("apple.com",),
    "tidal": ("tidal.com",),
    "deezer": ("deezer.com",),
}
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

SHORT_LINK_MAX = 1024 * 1024

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


# -- parsing (no network) ------------------------------------------------------


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
    """link.deezer.com carries the target in its ``dest`` parameter."""
    parts = urlsplit(url)
    if parts.hostname != "link.deezer.com":
        return None
    dest = parse_qs(parts.query).get("dest", [""])[0]
    return dest if dest.lower().startswith(("https://", "http://")) else None


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
