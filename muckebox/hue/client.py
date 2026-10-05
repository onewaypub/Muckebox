# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Talk to a Hue bridge over its local HTTPS API (v2).

The bridge's certificate is pinned at pairing: Muckebox stores its SHA-256
fingerprint, and every later connection must present the same certificate
(urllib3 ``assert_fingerprint``). The Hue root CA is not shipped.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import ssl
from typing import Any, Protocol

import requests
from requests.adapters import HTTPAdapter

from .errors import (
    HueCertificateChanged,
    HueError,
    HueLinkButton,
    HueNotFound,
    HueUnauthorized,
    HueUnreachable,
)
from .model import BridgeInfo, Pairing, Room, Scene

log = logging.getLogger(__name__)

TIMEOUT = 3.0  # seconds; the bridge answers in milliseconds on a LAN
PORT = 443
LINK_BUTTON = 101  # Hue error type: "link button not pressed"
MAX_NAME = 60


class HueBackend(Protocol):
    """One paired bridge (the real one or the fake)."""

    def rooms(self) -> list[Room]: ...

    def scenes(self) -> list[Scene]: ...

    def recall(self, scene_id: str) -> None: ...

    def off(self, grouped_light: str) -> None: ...


class HueConnector(Protocol):
    """Finding, pairing and opening bridges (the network or the fake)."""

    def find(self, ip: str | None) -> list[BridgeInfo]: ...

    def pair(self, ip: str) -> Pairing: ...

    def fingerprint(self, ip: str) -> str: ...

    def client(self, ip: str, key: str, fingerprint: str) -> HueBackend: ...


# -- TLS ----------------------------------------------------------------------------


def fingerprint_of(ip: str, timeout: float = TIMEOUT) -> str:
    """The SHA-256 fingerprint (hex) of the certificate the bridge presents."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # only to read it; it is pinned afterwards
    try:
        with (
            socket.create_connection((ip, PORT), timeout=timeout) as raw,
            context.wrap_socket(raw) as tls,
        ):
            der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as exc:
        raise HueUnreachable(f"{ip}: {exc}") from exc
    if not der:
        raise HueUnreachable(f"{ip}: no certificate")
    return hashlib.sha256(der).hexdigest()


class PinnedAdapter(HTTPAdapter):
    """Accept exactly one certificate: the one seen at pairing."""

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["assert_fingerprint"] = self.fingerprint
        super().init_poolmanager(*args, **kwargs)


def pinned_session(fingerprint: str) -> requests.Session:
    session = requests.Session()
    session.verify = False  # no CA check: the fingerprint is the check
    session.trust_env = False  # never through a proxy from the environment
    session.mount("https://", PinnedAdapter(fingerprint))
    return session


# -- requests -------------------------------------------------------------------------


def _send(session: Any, method: str, url: str, **kwargs: Any) -> Any:
    try:
        response = session.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.exceptions.SSLError as exc:
        if "fingerprint" in str(exc).lower():
            raise HueCertificateChanged(str(exc)) from exc
        raise HueUnreachable(str(exc)) from exc
    except requests.RequestException as exc:
        raise HueUnreachable(str(exc)) from exc
    if response.status_code in (401, 403):
        raise HueUnauthorized(f"HTTP {response.status_code}")
    if response.status_code == 404:
        raise HueNotFound(url.rsplit("/", 1)[-1])
    if response.status_code >= 400:
        raise HueError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise HueError("not JSON") from exc


def _name(value: object) -> str:
    text = " ".join("".join(c if c.isprintable() else " " for c in str(value or "")).split())
    return text[:MAX_NAME] or "?"


def read_info(session: Any, ip: str) -> BridgeInfo:
    """Identity of the bridge (needs no key)."""
    data = _send(session, "GET", f"https://{ip}/api/0/config")
    if not isinstance(data, dict) or not isinstance(data.get("bridgeid"), str):
        raise HueError("not a Hue bridge")
    return BridgeInfo(ip=ip, id=data["bridgeid"].lower(), name=_name(data.get("name")))


def pair(
    ip: str, devicetype: str, *, session: Any = None, fingerprint: str | None = None
) -> Pairing:
    """Ask for a key; works only within 30 s after the bridge's button was pressed."""
    fingerprint = fingerprint or fingerprint_of(ip)
    session = session or pinned_session(fingerprint)
    body = {"devicetype": devicetype[:40], "generateclientkey": True}
    data = _send(session, "POST", f"https://{ip}/api", json=body)
    entry = data[0] if isinstance(data, list) and data else {}
    if isinstance(entry, dict) and "error" in entry:
        if entry["error"].get("type") == LINK_BUTTON:
            raise HueLinkButton()
        raise HueError(str(entry["error"].get("description", "pairing failed")))
    key = entry.get("success", {}).get("username") if isinstance(entry, dict) else None
    if not isinstance(key, str) or not key:
        raise HueError("no key in the answer")
    return Pairing(info=read_info(session, ip), key=key, fingerprint=fingerprint)


class HueClient:
    """A paired bridge."""

    def __init__(self, ip: str, key: str, fingerprint: str, session: Any = None) -> None:
        self.ip = ip
        self._key = key
        self._session = session or pinned_session(fingerprint)

    def _resource(self, method: str, path: str, body: dict | None = None) -> list[dict]:
        url = f"https://{self.ip}/clip/v2/resource/{path}"
        headers = {"hue-application-key": self._key}
        data = _send(self._session, method, url, headers=headers, json=body)
        items = data.get("data") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def rooms(self) -> list[Room]:
        """Rooms and zones, with the light group that switches each."""
        result = []
        for kind in ("room", "zone"):
            for item in self._resource("GET", kind):
                services = item.get("services") or []
                group = next(
                    (s.get("rid") for s in services if s.get("rtype") == "grouped_light"), None
                )
                result.append(
                    Room(id=str(item.get("id")), name=_name(item.get("metadata", {}).get("name")),
                         grouped_light=group)
                )  # fmt: skip
        return result

    def scenes(self) -> list[Scene]:
        result = []
        for item in self._resource("GET", "scene"):
            group = item.get("group") or {}
            active = (item.get("status") or {}).get("active", "inactive") != "inactive"
            result.append(
                Scene(
                    id=str(item.get("id")),
                    name=_name(item.get("metadata", {}).get("name")),
                    room=group.get("rid") if group.get("rtype") in ("room", "zone") else None,
                    active=active,
                )
            )
        return result

    def recall(self, scene_id: str) -> None:
        self._resource("PUT", f"scene/{scene_id}", {"recall": {"action": "active"}})

    def off(self, grouped_light: str) -> None:
        self._resource("PUT", f"grouped_light/{grouped_light}", {"on": {"on": False}})


class HueNetwork:
    """The real network: mDNS search, pairing and pinned clients."""

    def __init__(self, devicetype: str) -> None:
        self.devicetype = devicetype

    def find(self, ip: str | None) -> list[BridgeInfo]:
        from .discovery import find_bridges

        return find_bridges(ip)

    def pair(self, ip: str) -> Pairing:
        return pair(ip, self.devicetype)

    def fingerprint(self, ip: str) -> str:
        return fingerprint_of(ip)

    def client(self, ip: str, key: str, fingerprint: str) -> HueBackend:
        return HueClient(ip, key, fingerprint)
