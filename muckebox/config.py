# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuration from environment variables.

Invalid Sonos or volume settings are not fatal: Muckebox still starts, shows
the problem on the tablet and refuses to control the speaker. Only settings
without which the web server cannot run at all (``PORT``, ``LISTEN``) raise
:class:`FatalConfigError`.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DEFAULT_MAX_VOLUME = 25
DEFAULT_VOLUME_STEP = 3
DEFAULT_DATA_DIR = "/data"
DEFAULT_PORT = 8484
# LISTEN values: which network interfaces the web server listens on.
# The default, LISTEN_ALL, lets tablets reach Muckebox over the home network.
LISTEN_ALL = "0.0.0.0"  # noqa: S104  # nosec B104
LISTEN_LOCALHOST = "127.0.0.1"
MIN_PIN_LENGTH = 4
# Example values from docker-compose.yml that must never work as a real PIN.
PLACEHOLDER_PINS = frozenset({"change-me", "changeme", "1234", "0000"})

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

# RFC 1123 host name: dot-separated labels of letters, digits and hyphens.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


class FatalConfigError(Exception):
    """A setting is invalid and the web server cannot start."""


@dataclass(frozen=True)
class ConfigProblem:
    """A non-fatal configuration problem.

    ``code`` is a stable key (also used for UI messages); ``detail`` is an
    English explanation for the log. Details never contain secret values.
    """

    code: str
    severity: Literal["error", "warning"]
    detail: str


@dataclass(frozen=True)
class Settings:
    sonos_room: str | None
    sonos_ip: str | None
    max_volume: int
    volume_step: int
    admin_pin: str | None = field(repr=False)  # never print the PIN
    data_dir: Path
    port: int
    listen: str = LISTEN_ALL
    problems: tuple[ConfigProblem, ...] = ()

    @property
    def sonos_config_ok(self) -> bool:
        """True if the speaker may be controlled with these settings."""
        return not any(p.severity == "error" for p in self.problems)

    @property
    def admin_locked(self) -> bool:
        return self.admin_pin is None


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Parse and validate settings from an environment mapping."""
    problems: list[ConfigProblem] = []

    def get(name: str) -> str | None:
        value = environ.get(name, "").strip()
        return value or None

    sonos_room = get("SONOS_ROOM")
    sonos_ip = get("SONOS_IP")
    if sonos_room is None and sonos_ip is None:
        problems.append(
            ConfigProblem("sonos_not_configured", "error", "Set SONOS_ROOM or SONOS_IP (or both).")
        )
    if sonos_ip is not None and not _is_ipv4_or_hostname(sonos_ip):
        problems.append(
            ConfigProblem(
                "sonos_ip_invalid",
                "error",
                "SONOS_IP must be an IPv4 address or a host name (no port, no URL).",
            )
        )

    max_volume = _parse_int(get("MAX_VOLUME"), DEFAULT_MAX_VOLUME, 1, 100)
    if max_volume is None:
        problems.append(
            ConfigProblem(
                "max_volume_invalid", "error", "MAX_VOLUME must be a whole number from 1 to 100."
            )
        )
        max_volume = DEFAULT_MAX_VOLUME

    volume_step = _parse_int(
        get("VOLUME_STEP"), min(DEFAULT_VOLUME_STEP, max_volume), 1, max_volume
    )
    if volume_step is None:
        problems.append(
            ConfigProblem(
                "volume_step_invalid",
                "error",
                f"VOLUME_STEP must be a whole number from 1 to MAX_VOLUME ({max_volume}).",
            )
        )
        volume_step = min(DEFAULT_VOLUME_STEP, max_volume)

    admin_pin = get("ADMIN_PIN")
    if admin_pin is None:
        problems.append(
            ConfigProblem(
                "admin_pin_missing",
                "warning",
                "ADMIN_PIN is not set; the parents' page is locked.",
            )
        )
    elif admin_pin.casefold() in PLACEHOLDER_PINS:
        problems.append(
            ConfigProblem(
                "admin_pin_placeholder",
                "warning",
                "ADMIN_PIN is still an example value; the parents' page is locked.",
            )
        )
        admin_pin = None
    elif len(admin_pin) < MIN_PIN_LENGTH:
        problems.append(
            ConfigProblem(
                "admin_pin_too_short",
                "warning",
                f"ADMIN_PIN must have at least {MIN_PIN_LENGTH} characters; "
                "the parents' page is locked.",
            )
        )
        admin_pin = None

    data_dir = Path(get("DATA_DIR") or DEFAULT_DATA_DIR).absolute()

    port = _parse_int(get("PORT"), DEFAULT_PORT, 1024, 65535)
    if port is None or port in _SONOS_PORTS or port in _NAS_PORTS or port in _BROWSER_BAD_PORTS:
        raise FatalConfigError(
            "PORT must be a number from 1024 to 65535 and must not be 1400-1499, "
            "a Synology DSM port (5000, 5001, 5357) or a port that browsers block."
        )

    listen = _parse_listen(get("LISTEN"))

    return Settings(
        sonos_room=sonos_room,
        sonos_ip=sonos_ip,
        max_volume=max_volume,
        volume_step=volume_step,
        admin_pin=admin_pin,
        data_dir=data_dir,
        port=port,
        listen=listen,
        problems=tuple(problems),
    )


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


def _parse_int(raw: str | None, default: int, low: int, high: int) -> int | None:
    """Return the integer value, ``default`` if unset, or None if invalid."""
    if raw is None:
        return default
    if not re.fullmatch(r"[+-]?\d+", raw):
        return None
    value = int(raw)
    return value if low <= value <= high else None


def _is_ipv4_or_hostname(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        # Reject things that look like (broken) IPv4 addresses, e.g. 192.0.2.300.
        if re.fullmatch(r"[\d.]+", value):
            return False
        return bool(_HOSTNAME_RE.match(value))
    return True
