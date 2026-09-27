# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The household's rooms and groups, read from one ZoneGroupState answer.

Any Sonos speaker returns the whole household's topology. Parsing it here,
instead of using SoCo's per-speaker properties, means that finding the room
costs exactly one request with a known timeout and never contacts other
speakers (which may be switched off).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from soco.xml import XML


@dataclass(frozen=True)
class Member:
    uid: str
    name: str
    ip: str
    visible: bool
    coordinator_uid: str


def parse_zone_group_state(xml: str) -> list[Member]:
    """Return every speaker in a ZoneGroupState document."""
    root = XML.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    groups = root.find("ZoneGroups")
    container = groups if groups is not None else root
    members = []
    for group in container.iter("ZoneGroup"):
        coordinator = group.get("Coordinator", "")
        for element in group.iter():
            if element.tag not in ("ZoneGroupMember", "Satellite"):
                continue
            ip = urlsplit(element.get("Location", "")).hostname
            if not ip or not element.get("UUID"):
                continue
            visible = (
                element.tag == "ZoneGroupMember"
                and element.get("Invisible") != "1"
                and element.get("IsZoneBridge") != "1"
            )
            members.append(
                Member(
                    uid=element.get("UUID", ""),
                    name=element.get("ZoneName", ""),
                    ip=ip,
                    visible=visible,
                    coordinator_uid=coordinator,
                )
            )
    return members
