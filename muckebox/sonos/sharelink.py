# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hand normalised share links to SoCo's ShareLinkPlugin.

Muckebox parses share links itself (see :mod:`muckebox.linkmeta`) because the
plugin's URL patterns miss several real-world formats. For playback it builds
one canonical URI per link that the plugin is known to understand.
"""

from __future__ import annotations

from .model import ShareLinkRef

SERVICES = ("spotify", "apple_music", "tidal", "deezer")

KINDS = {
    "spotify": ("album", "playlist", "track", "episode", "show"),
    "apple_music": ("album", "playlist", "song"),
    "tidal": ("album", "playlist", "track"),
    "deezer": ("album", "playlist", "track"),
}


def plugin_uri(link: ShareLinkRef) -> str:
    """Return a URI that SoCo's ShareLinkPlugin recognises for ``link``."""
    if link.kind not in KINDS.get(link.service, ()):
        raise ValueError(f"unsupported share link: {link.service} {link.kind}")
    if link.service == "spotify":
        return f"spotify:{link.kind}:{link.item_id}"
    if link.service == "tidal":
        return f"https://tidal.com/{link.kind}/{link.item_id}"
    if link.service == "deezer":
        return f"https://www.deezer.com/{link.kind}/{link.item_id}"
    # Apple Music: the storefront ("us") and the slug ("_") do not matter to
    # Sonos, only the catalogue ID does.
    if link.kind == "song":
        return f"https://music.apple.com/us/album/_/0?i={link.item_id}"
    return f"https://music.apple.com/us/{link.kind}/_/{link.item_id}"
