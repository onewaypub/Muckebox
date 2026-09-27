# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start-up configuration from environment variables.

Only what the web server needs before it can read anything else: where it
listens (``LISTEN``, ``PORT``) and where it keeps its data (``DATA_DIR``).
Everything else (room, volume limit, PIN) is set on the parents' page and
saved in ``DATA_DIR`` (see :mod:`muckebox.settings`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = "/data"
DEFAULT_PORT = 8484
# LISTEN values: which network interfaces the web server listens on.
# The default, LISTEN_ALL, lets tablets reach Muckebox over the home network.
LISTEN_ALL = "0.0.0.0"  # noqa: S104  # nosec B104
LISTEN_LOCALHOST = "127.0.0.1"
# Settings that earlier versions read from the environment. They are now set
# on the parents' page; if one is still set, the log says so.
LEGACY_VARIABLES = ("SONOS_ROOM", "SONOS_IP", "MAX_VOLUME", "VOLUME_STEP", "ADMIN_PIN")

# Ports Muckebox must not listen on:
# - 1400-1499: Sonos speakers use 1400, and SoCo's event listener (used by
#   other Sonos tools on the same host) binds a port in this range.
# - Common Synology DSM services.
# - Ports that browsers refuse to connect to ("bad ports" in the Fetch spec).
_SONOS_PORTS = range(1400, 1500)
_NAS_PORTS = {5000, 5001, 5357}
_BROWSER_BAD_PORTS = {
    1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566,
    6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080,
}  # fmt: skip


class FatalConfigError(Exception):
    """A setting is invalid and the web server cannot start."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int
    listen: str = LISTEN_ALL
    #: Names of old variables that are still set (never their values).
    legacy: tuple[str, ...] = ()


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Parse and validate the start-up settings from an environment mapping."""
    port = _parse_port(environ.get("PORT", "").strip() or None)
    listen = _parse_listen(environ.get("LISTEN", "").strip() or None)
    legacy = tuple(name for name in LEGACY_VARIABLES if environ.get(name, "").strip())
    return Settings(data_dir=data_dir_from(environ), port=port, listen=listen, legacy=legacy)


def data_dir_from(environ: Mapping[str, str]) -> Path:
    """The data folder named by ``DATA_DIR`` (default ``/data``)."""
    return Path(environ.get("DATA_DIR", "").strip() or DEFAULT_DATA_DIR).absolute()


def _parse_listen(raw: str | None) -> str:
    """``all`` (default), ``localhost`` or one IPv4 address of this host."""
    value = (raw or "all").lower()
    if value in ("all", "*", LISTEN_ALL):
        return LISTEN_ALL
    if value == "localhost":
        return LISTEN_LOCALHOST
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise FatalConfigError(
            "LISTEN must be 'all' (whole network), 'localhost' (this computer only) "
            "or an IPv4 address of this host."
        ) from None


def _parse_port(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_PORT
    port = int(raw) if re.fullmatch(r"\d{1,5}", raw) else None
    if (
        port is None
        or not 1024 <= port <= 65535
        or port in _SONOS_PORTS
        or port in _NAS_PORTS
        or port in _BROWSER_BAD_PORTS
    ):
        raise FatalConfigError(
            "PORT must be a number from 1024 to 65535 and must not be 1400-1499, "
            "a Synology DSM port (5000, 5001, 5357) or a port that browsers block."
        )
    return port
