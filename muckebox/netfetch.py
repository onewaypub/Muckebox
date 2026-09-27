# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound HTTPS requests to the internet, and nothing else.

Every request Muckebox makes to a music service (share-link resolution,
titles, cover images) goes through :class:`Fetcher`. URLs come from parents
pasting links and from the services' own responses, so the fetcher guards
against server-side request forgery and against slow or huge responses:

* only ``https`` on the default port, no user info in the URL;
* the host must be on an allowlist of domains (``"spotify.com"`` also allows
  ``"open.spotify.com"``);
* redirects are followed by hand, at most five, and every hop is checked
  again (scheme, host, addresses);
* every address the host name resolves to must be a global unicast address,
  so a public name that points into the home network is refused;
* per-request timeouts plus a total deadline, and a cap on the number of
  (decompressed) body bytes;
* no cookies are stored, no proxy or ``.netrc`` settings are taken from the
  environment, and no ``Accept-Language`` header is sent.

The address check is defence in depth: ``requests`` resolves the name once
more when it connects, so the host allowlist remains the main protection.
"""

from __future__ import annotations

import http.cookiejar
import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from urllib3.exceptions import ReadTimeoutError

from . import __version__

#: Sites such as open.spotify.com serve ``og:`` tags only to non-browser
#: clients, and Spotify's short-link service redirects straight to
#: open.spotify.com only when the User-Agent *starts* with
#: ``python-requests/``. The project name follows so that operators can
#: still see who is calling.
USER_AGENT = (
    f"python-requests/{requests.__version__} Muckebox/{__version__}"
    " (+https://github.com/onewaypub/Muckebox)"
)

#: (connect, read) timeout of a single request in seconds.
TIMEOUT = (3.05, 5.0)
#: Wall-clock limit for one :meth:`Fetcher.get` call including redirects.
DEADLINE = 10.0
MAX_REDIRECTS = 5

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_METHODS = frozenset({"GET", "HEAD"})
_CHUNK = 64 * 1024

Resolver = Callable[[str, int], Iterable[str]]


class FetchError(Exception):
    """A request was refused or failed.

    ``code`` is one of ``bad_url``, ``not_https``, ``host_not_allowed``,
    ``dns_failed``, ``address_not_allowed``, ``too_many_redirects``,
    ``too_large``, ``timeout``, ``connection_failed`` or ``bad_status``.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    """The last response of a request.

    ``url`` is the URL of this response (after redirects), ``headers`` has
    lower-case names. ``location`` is set only when a redirect was *not*
    followed because ``stop_redirect`` asked to stop: it is the absolute
    redirect target, which has not been validated or fetched.
    """

    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    location: str | None = None


def system_resolver(host: str, port: int) -> list[str]:
    """Return the addresses the operating system resolves ``host`` to."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def host_allowed(host: str, allow: Iterable[str]) -> bool:
    """True if ``host`` is one of the ``allow`` domains or a subdomain of one."""
    host = host.lower().rstrip(".")
    for domain in allow:
        domain = domain.lower().strip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def _global_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


class Fetcher:
    """Make restricted HTTPS requests; see the module documentation.

    ``allow`` is the default domain allowlist; :meth:`get` can pass its own.
    ``session``, ``resolver`` and ``clock`` exist for tests. The session's
    cookie policy is replaced by one that rejects all cookies.
    """

    def __init__(
        self,
        allow: Iterable[str] = (),
        *,
        session: requests.Session | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
        timeout: tuple[float, float] = TIMEOUT,
        deadline: float = DEADLINE,
        max_redirects: int = MAX_REDIRECTS,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.allow = tuple(allow)
        if session is None:
            session = requests.Session()
            session.trust_env = False
        # An empty domain allowlist: no cookie is ever stored.
        session.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        self._session = session
        self._resolver = resolver or system_resolver
        self._clock = clock
        self._timeout = timeout
        self._deadline = deadline
        self._max_redirects = max_redirects
        self._user_agent = user_agent

    def close(self) -> None:
        self._session.close()

    def get(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str | None = None,
        method: str = "GET",
        allow: Iterable[str] | None = None,
        stop_at: bytes | None = None,
        truncate: bool = False,
        stop_redirect: Callable[[str], bool] | None = None,
    ) -> FetchResult:
        """Fetch ``url`` and return the final response.

        Responses of any status are returned; only transport problems and
        policy violations raise :class:`FetchError`. ``allow`` replaces the
        instance allowlist for this call.

        The body is read up to ``max_bytes``; more raises ``too_large``, or
        with ``truncate`` returns the first ``max_bytes`` bytes. With
        ``stop_at``, reading ends right after the first occurrence of that
        marker (compared case-insensitively), e.g. ``b"</head>"``. A
        ``HEAD`` request returns an empty body.

        ``stop_redirect`` is called with each absolute redirect target before
        it is checked and followed; if it returns true, the redirect response
        is returned with ``location`` set instead.
        """
        method = method.upper()
        if method not in _METHODS:
            raise ValueError(f"unsupported method: {method}")
        domains = self.allow if allow is None else tuple(allow)
        headers = {"User-Agent": self._user_agent, "Accept": accept or "*/*"}
        started = self._clock()
        redirects = 0
        while True:
            host, port = self._check_url(url, domains)
            self._check_addresses(host, port)
            response = self._send(method, url, headers, started)
            try:
                location = response.headers.get("location")
                if response.status_code in _REDIRECTS and location:
                    target = urljoin(url, location.strip())
                    if stop_redirect is not None and stop_redirect(target):
                        return self._result(url, response, b"", location=target)
                    redirects += 1
                    if redirects > self._max_redirects:
                        raise FetchError("too_many_redirects", f"more than {self._max_redirects}")
                    if response.status_code == 303:
                        method = "HEAD" if method == "HEAD" else "GET"
                    url = target
                    continue
                body = b""
                if method != "HEAD":
                    body = self._read(response, max_bytes, stop_at, truncate, started)
                return self._result(url, response, body)
            finally:
                response.close()

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _check_url(url: str, domains: tuple[str, ...]) -> tuple[str, int]:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise FetchError("bad_url", str(exc)) from exc
        if parts.scheme.lower() != "https":
            raise FetchError("not_https", f"only https is allowed: {parts.scheme!r}")
        host = (parts.hostname or "").rstrip(".")
        if not host or parts.username is not None or parts.password is not None:
            raise FetchError("bad_url", "missing host or user info in URL")
        if port not in (None, 443):
            raise FetchError("bad_url", f"port {port} is not allowed")
        if not host_allowed(host, domains):
            raise FetchError("host_not_allowed", f"host not allowed: {host}")
        return host, 443

    def _check_addresses(self, host: str, port: int) -> None:
        try:
            addresses = list(self._resolver(host, port))
        except (OSError, UnicodeError) as exc:
            raise FetchError("dns_failed", f"cannot resolve {host}: {exc}") from exc
        if not addresses:
            raise FetchError("dns_failed", f"no addresses for {host}")
        for address in addresses:
            if not _global_address(address):
                raise FetchError("address_not_allowed", f"{host} resolves to {address}")

    def _remaining(self, started: float) -> float:
        remaining = self._deadline - (self._clock() - started)
        if remaining <= 0:
            raise FetchError("timeout", "deadline exceeded")
        return remaining

    def _send(
        self, method: str, url: str, headers: dict[str, str], started: float
    ) -> requests.Response:
        remaining = self._remaining(started)
        connect, read = self._timeout
        try:
            return self._session.request(
                method,
                url,
                headers=headers,
                timeout=(min(connect, remaining), min(read, remaining)),
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise FetchError("timeout", str(exc)) from exc
        except requests.RequestException as exc:
            raise FetchError("connection_failed", str(exc)) from exc

    def _read(
        self,
        response: requests.Response,
        max_bytes: int,
        stop_at: bytes | None,
        truncate: bool,
        started: float,
    ) -> bytes:
        try:
            length = int(response.headers.get("content-length", ""))
        except ValueError:
            length = None
        if not truncate and length is not None and length > max_bytes:
            raise FetchError("too_large", f"{length} bytes announced")
        marker = stop_at.lower() if stop_at else b""
        data = bytearray()
        try:
            for chunk in response.iter_content(_CHUNK):
                start = max(0, len(data) - len(marker) + 1)
                data += chunk
                end = None
                if marker:
                    found = bytes(data[start:]).lower().find(marker)
                    if found >= 0:
                        end = start + found + len(marker)
                if (len(data) if end is None else end) > max_bytes:
                    if truncate:
                        return bytes(data[:max_bytes])
                    raise FetchError("too_large", f"more than {max_bytes} bytes")
                if end is not None:
                    return bytes(data[:end])
                self._remaining(started)
        except requests.RequestException as exc:
            # requests reports a read timeout while streaming as a
            # ConnectionError that wraps urllib3's ReadTimeoutError.
            cause = exc.args[0] if exc.args else None
            timed_out = isinstance(exc, requests.Timeout) or isinstance(cause, ReadTimeoutError)
            raise FetchError("timeout" if timed_out else "connection_failed", str(exc)) from exc
        return bytes(data)

    @staticmethod
    def _result(
        url: str, response: requests.Response, body: bytes, location: str | None = None
    ) -> FetchResult:
        headers = {name.lower(): value for name, value in response.headers.items()}
        return FetchResult(
            url=url, status=response.status_code, headers=headers, body=body, location=location
        )
