# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Creating tiles from share links (called by the parents' API)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from muckebox import linkmeta
from muckebox.covers import CoverError
from muckebox.library import MAX_TITLE_LENGTH, LibraryError, sharelink_source
from muckebox.netfetch import Fetcher, FetchError

from .errors import ApiError

log = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048
#: Services whose share links have been tested on real Sonos hardware.
TESTED_SERVICES = frozenset({"apple_music"})
_FALLBACK_TITLES = {
    "spotify": "Spotify",
    "apple_music": "Apple Music",
    "tidal": "TIDAL",
    "deezer": "Deezer",
}

#: Builds the fetcher for one request (tests replace it).
fetcher_factory: Callable[[], Fetcher] = Fetcher


def create_tile(services, url: str):
    """Resolve a share link, fetch its title and cover, and add a tile."""
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ApiError(400, "bad_request")
    warnings: list[str] = []
    fetcher = fetcher_factory()
    try:
        try:
            parsed = linkmeta.resolve(url, fetcher)
        except linkmeta.LinkError as exc:
            raise ApiError(422, exc.code) from exc
        try:
            meta = linkmeta.metadata(parsed, fetcher)
        except Exception:
            # Title and cover are a convenience; never let them block the tile.
            log.exception("Looking up the share link's title and cover failed")
            meta = linkmeta.LinkMeta(title=None, image_url=None)
        cover = None
        if meta.image_url:
            try:
                cover = services.covers.save(linkmeta.fetch_image(meta.image_url, fetcher))
            except (FetchError, CoverError) as exc:
                log.info("No cover for share link: %s", exc)
    finally:
        fetcher.close()

    title = (meta.title or "").strip()
    if not title:
        title = _FALLBACK_TITLES.get(parsed.ref.service, "Musik")
        warnings.append("title_missing")
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    if cover is None:
        warnings.append("cover_missing")
    if parsed.ref.service not in TESTED_SERVICES:
        warnings.append("sharelink_experimental")
    try:
        tile = services.library.add(
            title, sharelink_source(parsed.ref, parsed.canonical_url), cover
        )
    except LibraryError as exc:
        raise ApiError(422, exc.code) from exc
    return tile, warnings
