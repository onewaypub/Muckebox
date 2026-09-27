# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stand-ins for SoCo objects, and hand-written DIDL fixtures.

The fixtures mimic what Sonos returns; all IDs, addresses and titles are
made up.
"""

from __future__ import annotations

from types import SimpleNamespace
from xml.sax.saxutils import escape

from soco.data_structures_entry import from_didl_string

NS = (
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"'
)


def res_md(item_id, title, item_class, service="SA_RINCON52231_X_#Svc52231-0-Token"):
    class_tag = f"<upnp:class>{item_class}</upnp:class>" if item_class else ""
    return (
        f'<DIDL-Lite {NS}><item id="{item_id}" parentID="-1" restricted="true">'
        f"<dc:title>{escape(title)}</dc:title>{class_tag}"
        f'<desc id="cdudn" nameSpace="urn:schemas-rinconnetworks-com:metadata-1-0/">'
        f"{service}</desc></item></DIDL-Lite>"
    )


def favorite_xml(
    number,
    title,
    uri,
    meta,
    *,
    fav_type="instantPlay",
    description="Apple Music",
    art="/getaa?s=1&u=x-sonos-http%3aart",
):
    res = (
        f'<res protocolInfo="{uri.split(":", 1)[0]}:*:*:*">{escape(uri)}</res>'
        if uri
        else "<res></res>"
    )
    return (
        f'<item id="FV:2/{number}" parentID="FV:2" restricted="false">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.itemobject.item.sonos-favorite</upnp:class>"
        f"<r:ordinal>{number}</r:ordinal>{res}"
        f"<upnp:albumArtURI>{escape(art)}</upnp:albumArtURI>"
        f"<r:type>{fav_type}</r:type><r:description>{description}</r:description>"
        + (f"<r:resMD>{escape(meta)}</r:resMD>" if meta is not None else "")
        + "</item>"
    )


ALBUM_URI = "x-rincon-cpcontainer:1004206calbum%3a1001?sid=204&flags=8300&sn=3"
RADIO_URI = "x-sonosapi-stream:s00001?sid=254&flags=8224&sn=0"
NAS_URI = "x-rincon-playlist:RINCON_000000000000001400#S://nas/music/Kids"

FAVORITES_XML = (
    f"<DIDL-Lite {NS}>"
    + favorite_xml(
        1,
        "Bibi & Tina",
        ALBUM_URI,
        res_md("1004206calbum%3a1001", "Bibi & Tina", "object.container.album.musicAlbum"),
    )
    + favorite_xml(
        2,
        "Kinderradio",
        RADIO_URI,
        res_md(
            "F00092020s00001",
            "Kinderradio",
            "object.item.audioItem.audioBroadcast",
            service="SA_RINCON65031_",
        ),
        description="TuneIn",
    )
    + favorite_xml(3, "NAS Kids", NAS_URI, res_md("S://nas/music/Kids", "NAS Kids", ""))
    + favorite_xml(
        4,
        "Artist shortcut",
        "",
        res_md("artist", "Artist", "object.container.person.musicArtist"),
        fav_type="shortcut",
    )
    + favorite_xml(
        5,
        "Broken service item",
        "x-sonos-http:song%3a1.mp4?sid=204",
        res_md("x", "Broken", ""),
    )
    + "</DIDL-Lite>"
)


def parsed_favorites():
    return from_didl_string(FAVORITES_XML)


class FakeService:
    """Records UPnP calls; answers from ``responses``; raises from ``errors``."""

    def __init__(self, name, responses=None):
        self.name = name
        self.calls = []
        self.responses = responses or {}
        self.errors = {}

    def __getattr__(self, action):
        if action.startswith("_"):
            raise AttributeError(action)

        def call(args, timeout=None):
            self.calls.append((action, dict(args), timeout))
            errors = self.errors.get(action)
            if errors:
                raise errors.pop(0) if isinstance(errors, list) else errors
            response = self.responses.get(action, {})
            return response(dict(args)) if callable(response) else response

        return call

    def actions(self):
        return [call[0] for call in self.calls]


class FakeZone:
    def __init__(self, name, ip, uid, visible=True):
        self.player_name = name
        self.ip_address = ip
        self.uid = uid
        self.is_visible = visible
        self.avTransport = FakeService(
            "avTransport",
            {
                "AddURIToQueue": {"FirstTrackNumberEnqueued": "1"},
                "GetTransportInfo": {"CurrentTransportState": "PLAYING"},
                "GetMediaInfo": {"CurrentURI": ""},
                "GetCurrentTransportActions": {
                    "Actions": "Set, Stop, Pause, Play, X_DLNA_SeekTime, Next, Previous"
                },
            },
        )
        self.renderingControl = FakeService(
            "renderingControl",
            {
                "GetVolume": {"CurrentVolume": "12"},
                "GetSupportsOutputFixed": {"CurrentSupportsFixed": "0"},
            },
        )
        self.contentDirectory = FakeService(
            "contentDirectory", {"Browse": {"Result": "", "TotalMatches": "0"}}
        )
        self.zoneGroupTopology = FakeService("zoneGroupTopology")
        self.music_library = SimpleNamespace(get_sonos_favorites=lambda **kwargs: [])

    def __repr__(self):
        return f"FakeZone({self.player_name!r})"


def zone_group_state(groups):
    """ZoneGroupState XML for ``groups``: a list of (coordinator, members...)."""
    parts = ["<ZoneGroupState><ZoneGroups>"]
    for coordinator, *members in groups:
        parts.append(f'<ZoneGroup Coordinator="{coordinator.uid}" ID="{coordinator.uid}:1">')
        for member in members:
            invisible = "" if member.is_visible else ' Invisible="1"'
            parts.append(
                f'<ZoneGroupMember UUID="{member.uid}" '
                f'Location="http://{member.ip_address}:1400/xml/device_description.xml" '
                f'ZoneName="{escape(member.player_name)}"{invisible}/>'
            )
        parts.append("</ZoneGroup>")
    parts.append("</ZoneGroups><VanishedDevices/></ZoneGroupState>")
    return "".join(parts)


def household(*zones, groups=None):
    """Wire zones into a household. ``groups`` lists (coordinator, members...).

    Every zone answers GetZoneGroupState with the whole household.
    """
    groups = groups or [(zone, zone) for zone in zones]
    xml = zone_group_state(groups)
    for zone in zones:
        zone.zoneGroupTopology.responses["GetZoneGroupState"] = {"ZoneGroupState": xml}
    return {zone.ip_address: zone for zone in zones}


def switch_off(zone):
    """Make every request to ``zone`` time out, like an unplugged speaker."""
    import requests

    for service in (
        zone.avTransport,
        zone.renderingControl,
        zone.contentDirectory,
        zone.zoneGroupTopology,
    ):
        service.errors = {
            name: requests.exceptions.ConnectTimeout("off")
            for name in (
                "GetZoneGroupState",
                "GetVolume",
                "SetVolume",
                "Play",
                "GetTransportInfo",
            )
        }
