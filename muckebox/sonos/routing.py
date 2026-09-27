# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Decide how a favorite must be started (pure functions, no network)."""

from __future__ import annotations

import re

from .model import Route

# Music sources derived from the URI scheme (same rules as SoCo's
# SoCo.music_source_from_uri, kept here so routing stays a pure function).
_SOURCES = [
    (r"^x-rincon-mp3radio:", "RADIO"),
    (r"^x-sonosapi-stream:", "RADIO"),
    (r"^x-sonosapi-radio:", "RADIO"),
    (r"^x-sonosapi-hls:", "RADIO"),
    (r"^x-sonos-http:sonos", "RADIO"),
    (r"^aac:", "RADIO"),
    (r"^hls-radio:", "RADIO"),
    (r"^x-rincon-stream:", "LINE_IN"),
    (r"^x-sonos-htastream:", "TV"),
    (r"^x-file-cifs:", "LIBRARY"),
    (r"^x-rincon-playlist:", "LIBRARY"),
    (r"^file:///jffs/settings/savedqueues", "SONOS_PLAYLIST"),
]

# Classes that play directly, so that Sonos can resume where the listener
# stopped: audiobooks and single podcast episodes.
_RESUMABLE_CLASS_RE = re.compile(r"\.(audioBook|podcast|recentShow)\b", re.IGNORECASE)


def music_source(uri: str) -> str:
    for pattern, source in _SOURCES:
        if re.match(pattern, uri):
            return source
    return "UNKNOWN"


def classify(uri: str, item_class: str) -> tuple[Route, str | None]:
    """Return the playback route for a favorite and, if unplayable, why."""
    if not uri:
        return Route.UNSUPPORTED, "no_resource"
    source = music_source(uri)
    if source == "TV":
        return Route.UNSUPPORTED, "tv_input"
    if source in ("RADIO", "LINE_IN"):
        return Route.DIRECT, None
    if item_class.startswith("object.item.audioItem.audioBroadcast"):
        return Route.DIRECT, None
    if item_class.startswith("object.item") and _RESUMABLE_CLASS_RE.search(item_class):
        return Route.DIRECT, None
    return Route.QUEUE, None


def other_route(route: Route) -> Route:
    """The route to try once when the speaker rejects the first one."""
    return Route.QUEUE if route is Route.DIRECT else Route.DIRECT
