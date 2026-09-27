# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local time, for usage windows and the daily game time.

The time zone comes from, in this order:

1. the parents' page (the zone of the parent's browser, saved in settings),
2. the ``TZ`` environment variable (``Europe/Berlin`` or ``:Europe/Berlin``),
3. the system (the name behind ``/etc/localtime``),
4. otherwise UTC.

POSIX rules such as ``CET-1CEST,M3.5.0,M10.5.0/3`` are not supported: with
fixed offsets the windows would be wrong after a daylight saving change.
The ``tzdata`` package provides the zone database where the system has none.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

_ZONE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")
_LOCALTIME = Path("/etc/localtime")


@dataclass(frozen=True)
class Zone:
    tz: tzinfo
    name: str
    #: Where the zone came from: "settings", "TZ", "system" or "default".
    source: str


def load_zone(name: object) -> tzinfo | None:
    """The zone called ``name``, or None if it is not a known IANA zone."""
    if not isinstance(name, str) or len(name) > 64 or not _ZONE_RE.match(name):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


class ZoneResolver:
    """Finds the zone to use; logs a problem with ``TZ`` only once."""

    def __init__(self, environ: Mapping[str, str] = os.environ, localtime: Path = _LOCALTIME):
        self._lock = threading.Lock()
        self._cache: dict[str | None, Zone] = {}
        self._fallback = self._from_environment(environ, localtime)

    def zone(self, configured: str | None = None) -> Zone:
        if not configured:
            return self._fallback
        with self._lock:
            if configured not in self._cache:
                tz = load_zone(configured)
                self._cache[configured] = (
                    Zone(tz, configured, "settings") if tz is not None else self._fallback
                )
            return self._cache[configured]

    @staticmethod
    def _from_environment(environ: Mapping[str, str], localtime: Path) -> Zone:
        raw = environ.get("TZ", "").strip().removeprefix(":")
        if raw:
            tz = load_zone(raw)
            if tz is not None:
                return Zone(tz, raw, "TZ")
            log.warning(
                "TZ=%s is not a time zone name such as Europe/Berlin; ignoring it", raw[:64]
            )
        name = _system_zone_name(localtime)
        tz = load_zone(name) if name else None
        if tz is not None and name:
            return Zone(tz, name, "system")
        return Zone(UTC, "UTC", "default")


def _system_zone_name(localtime: Path) -> str | None:
    try:
        target = os.readlink(localtime)
    except OSError:
        return None
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else None


def local_time(epoch: float, zone: Zone) -> datetime:
    return datetime.fromtimestamp(epoch, zone.tz)
