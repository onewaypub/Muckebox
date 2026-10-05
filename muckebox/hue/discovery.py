# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find Hue bridges: by mDNS (``_hue._tcp``) or at an address the parents enter.

mDNS works wherever the Hue app finds the bridge: in the same network, or
across VLANs through an mDNS reflector/repeater. The Hue cloud discovery is
never used. Every bridge found is asked for its identity (no key needed).
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from collections.abc import Callable

from .client import fingerprint_of, pinned_session, read_info
from .errors import HueError
from .model import BridgeInfo

log = logging.getLogger(__name__)

SERVICE = "_hue._tcp.local."
SEARCH_SECONDS = 3.0


def browse_mdns(timeout: float = SEARCH_SECONDS) -> list[str]:
    """IPv4 addresses of the bridges that answer within ``timeout`` seconds."""
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

    found: list[str] = []
    lock = threading.Lock()
    zeroconf = Zeroconf()

    # zeroconf calls handlers with these keyword names.
    def on_change(zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info is None:
            return
        with lock:
            for address in info.parsed_addresses():
                if ipaddress.ip_address(address).version == 4 and address not in found:
                    found.append(address)

    try:
        ServiceBrowser(zeroconf, SERVICE, handlers=[on_change])
        threading.Event().wait(timeout)
    finally:
        zeroconf.close()
    return found


def identify(ip: str) -> BridgeInfo:
    """Who answers at ``ip`` (raises HueError if it is no Hue bridge)."""
    return read_info(pinned_session(fingerprint_of(ip)), ip)


def find_bridges(
    ip: str | None,
    *,
    browse: Callable[[], list[str]] = browse_mdns,
    ask: Callable[[str], BridgeInfo] = identify,
) -> list[BridgeInfo]:
    """The bridge at ``ip``, or all bridges found by mDNS."""
    if ip is not None:
        return [ask(ip)]  # errors reach the parents: they typed this address
    bridges = []
    for address in browse():
        try:
            bridges.append(ask(address))
        except HueError as exc:
            log.info("Found %s by mDNS, but it did not answer like a bridge: %s", address, exc)
    return bridges
