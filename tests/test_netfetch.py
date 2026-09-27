# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import io
import socket
from http.client import HTTPMessage
from types import SimpleNamespace

import pytest
import requests
import requests_mock
from requests.cookies import extract_cookies_to_jar

from muckebox import netfetch
from muckebox.netfetch import Fetcher, FetchError, FetchResult, host_allowed

# Public anycast resolver addresses: they must count as global. The
# documentation ranges (192.0.2.0/24 etc.) are *not* global.
PUBLIC_V4 = "1.1.1.1"
PUBLIC_V6 = "2606:4700:4700::1111"
PRIVATE_V4 = ".".join(["10", "0", "0", "1"])
HOME_V4 = ".".join(["192", "168", "0", "10"])
CGNAT_V4 = ".".join(["100", "64", "0", "1"])


class Resolver:
    def __init__(self, mapping=None, default=(PUBLIC_V4,)):
        self.mapping = mapping or {}
        self.default = default
        self.calls = []

    def __call__(self, host, port):
        self.calls.append((host, port))
        return list(self.mapping.get(host, self.default))


class Clock:
    """Returns the given times on successive calls, then keeps the last one."""

    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]


class Breaking(io.RawIOBase):
    """A response body that yields some bytes, then raises ``exc``."""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        self.calls += 1
        if self.calls == 1:
            buffer[:3] = b"abc"
            return 3
        raise self.exc


def make(allow=("example.com",), **kwargs):
    kwargs.setdefault("resolver", Resolver())
    return Fetcher(allow, **kwargs)


@pytest.fixture
def http():
    with requests_mock.Mocker() as mocker:
        yield mocker


# -- basic requests ----------------------------------------------------------


def test_get_returns_status_headers_body_and_final_url(http):
    http.get(
        "https://www.example.com/page",
        content=b"<html>ok</html>",
        headers={"Content-Type": "text/html", "X-Thing": "1"},
    )
    result = make().get("https://www.example.com/page", max_bytes=1000)
    assert result == FetchResult(
        url="https://www.example.com/page",
        status=200,
        headers={"content-type": "text/html", "x-thing": "1"},
        body=b"<html>ok</html>",
    )


def test_request_uses_non_browser_user_agent_timeouts_and_manual_redirects(http):
    http.get("https://example.com/", content=b"")
    session = requests.Session()
    options = []
    send = session.request

    def spy(*args, **kwargs):
        options.append(kwargs)
        return send(*args, **kwargs)

    session.request = spy
    make(session=session).get("https://example.com/", max_bytes=10, accept="application/json")
    request = http.last_request
    agent = request.headers["User-Agent"]
    assert agent.startswith("python-requests/")
    assert "Muckebox/" in agent
    assert "Mozilla" not in agent
    assert request.headers["Accept"] == "application/json"
    assert "Accept-Language" not in request.headers
    assert request.timeout == (3.05, 5.0)
    assert request.stream is True
    assert [kwargs["allow_redirects"] for kwargs in options] == [False]


def test_default_accept_header(http):
    http.get("https://example.com/", content=b"")
    make().get("https://example.com/", max_bytes=10)
    assert http.last_request.headers["Accept"] == "*/*"


def test_error_responses_are_returned_not_raised(http):
    http.get("https://example.com/missing", status_code=404, content=b"gone")
    result = make().get("https://example.com/missing", max_bytes=100)
    assert (result.status, result.body) == (404, b"gone")


def test_head_request_returns_empty_body(http):
    http.head("https://example.com/", headers={"Content-Length": "999999"})
    result = make().get("https://example.com/", max_bytes=10, method="head")
    assert result.body == b""
    assert http.last_request.method == "HEAD"


def test_only_get_and_head_are_supported():
    with pytest.raises(ValueError, match="unsupported method"):
        make().get("https://example.com/", max_bytes=10, method="POST")


def test_fetch_error_carries_code_and_message():
    error = FetchError("too_large", "more than 10 bytes")
    assert error.code == "too_large"
    assert str(error) == "more than 10 bytes"
    assert str(FetchError("timeout")) == "timeout"


# -- URL policy --------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://example.com/", "not_https"),
        ("ftp://example.com/", "not_https"),
        ("file:///etc/passwd", "not_https"),
        ("//example.com/", "not_https"),
        ("https:///path-only", "bad_url"),
        ("https://user@example.com/", "bad_url"),
        ("https://user:secret@example.com/", "bad_url"),
        ("https://example.com:8443/", "bad_url"),
        ("https://example.com:0/", "bad_url"),
        ("https://example.com:port/", "bad_url"),
        ("https://[::1/", "bad_url"),
        ("https://example.org/", "host_not_allowed"),
        ("https://notexample.com/", "host_not_allowed"),
        ("https://example.com.attacker.example/", "host_not_allowed"),
        ("https://" + PUBLIC_V4 + "/", "host_not_allowed"),
    ],
)
def test_urls_outside_the_policy_are_refused_without_a_request(http, url, code):
    resolver = Resolver()
    with pytest.raises(FetchError) as info:
        make(resolver=resolver).get(url, max_bytes=10)
    assert info.value.code == code
    assert http.call_count == 0
    assert resolver.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://EXAMPLE.com/",
        "https://deep.sub.example.com/x",
        "https://example.com:443/",
        "https://example.com./",
    ],
)
def test_allowed_urls(http, url):
    http.get(requests_mock.ANY, content=b"ok")
    assert make().get(url, max_bytes=10).body == b"ok"


def test_host_allowed_matches_domains_and_their_subdomains():
    allow = ["spotify.com", "resources.tidal.com", ""]
    assert host_allowed("open.spotify.com", allow)
    assert host_allowed("spotify.com", allow)
    assert host_allowed("OPEN.Spotify.COM.", allow)
    assert host_allowed("resources.tidal.com", allow)
    assert not host_allowed("tidal.com", allow)
    assert not host_allowed("evilspotify.com", allow)
    assert not host_allowed("spotify.com.example", allow)
    assert not host_allowed("anything", [])


def test_per_call_allowlist_replaces_the_instance_allowlist(http):
    http.get("https://example.org/", content=b"ok")
    fetcher = make(allow=["example.com"])
    assert fetcher.get("https://example.org/", max_bytes=10, allow=["example.org"]).body == b"ok"
    with pytest.raises(FetchError) as info:
        fetcher.get("https://example.com/", max_bytes=10, allow=["example.org"])
    assert info.value.code == "host_not_allowed"


def test_empty_allowlist_refuses_everything(http):
    with pytest.raises(FetchError) as info:
        Fetcher(resolver=Resolver()).get("https://example.com/", max_bytes=10)
    assert info.value.code == "host_not_allowed"


# -- DNS checks ----------------------------------------------------------------


@pytest.mark.parametrize(
    "addresses",
    [
        [PRIVATE_V4],
        [HOME_V4],
        ["127.0.0.1"],
        ["::1"],
        ["169.254.169.254"],
        ["fe80::1%eth0"],
        ["0.0.0.0"],  # noqa: S104
        ["192.0.2.10"],
        [CGNAT_V4],
        ["224.0.0.251"],
        ["ff0e::1"],
        ["::ffff:" + PRIVATE_V4],
        ["fd00::1"],
        [PUBLIC_V4, PRIVATE_V4],
        ["not an address"],
    ],
)
def test_hosts_resolving_to_non_global_addresses_are_refused(http, addresses):
    resolver = Resolver(default=addresses)
    with pytest.raises(FetchError) as info:
        make(resolver=resolver).get("https://example.com/", max_bytes=10)
    assert info.value.code == "address_not_allowed"
    assert resolver.calls == [("example.com", 443)]
    assert http.call_count == 0


@pytest.mark.parametrize("addresses", [[PUBLIC_V4], [PUBLIC_V6], [PUBLIC_V4, PUBLIC_V6]])
def test_global_addresses_are_accepted(http, addresses):
    http.get("https://example.com/", content=b"ok")
    assert make(resolver=Resolver(default=addresses)).get("https://example.com/", max_bytes=10)


def test_resolution_failures_are_fetch_errors(http):
    def failing(host, port):
        raise socket.gaierror(-2, "Name or service not known")

    def bad_name(host, port):
        raise UnicodeError("label too long")

    for resolver in (failing, bad_name, Resolver(default=[])):
        with pytest.raises(FetchError) as info:
            make(resolver=resolver).get("https://example.com/", max_bytes=10)
        assert info.value.code == "dns_failed"
    assert http.call_count == 0


def test_system_resolver_uses_getaddrinfo(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, port, type=0):
        calls.append((host, port, type))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_V4, port)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (PUBLIC_V6, port, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert netfetch.system_resolver("example.com", 443) == [PUBLIC_V4, PUBLIC_V6]
    assert calls == [("example.com", 443, socket.SOCK_STREAM)]


def test_fetcher_uses_the_system_resolver_by_default(http, monkeypatch):
    monkeypatch.setattr(netfetch, "system_resolver", lambda host, port: [HOME_V4])
    with pytest.raises(FetchError) as info:
        Fetcher(["example.com"]).get("https://example.com/", max_bytes=10)
    assert info.value.code == "address_not_allowed"


# -- redirects -----------------------------------------------------------------


def test_redirects_are_followed_across_allowed_hosts(http):
    http.get(
        "https://short.example.com/x",
        status_code=301,
        headers={"Location": "https://hop.example.org/y"},
    )
    http.get("https://hop.example.org/y", status_code=302, headers={"Location": "/z?a=1"})
    http.get("https://hop.example.org/z?a=1", content=b"final")
    resolver = Resolver()
    fetcher = make(allow=["example.com", "example.org"], resolver=resolver)
    result = fetcher.get("https://short.example.com/x", max_bytes=100)
    assert (result.url, result.status, result.body) == (
        "https://hop.example.org/z?a=1",
        200,
        b"final",
    )
    assert [host for host, _ in resolver.calls] == [
        "short.example.com",
        "hop.example.org",
        "hop.example.org",
    ]


@pytest.mark.parametrize(
    ("location", "final"),
    [
        ("/album/1", "https://example.com/album/1"),
        ("album/1", "https://example.com/intl-de/album/1"),
        ("../track/2", "https://example.com/track/2"),
        ("//www.example.com/show/3", "https://www.example.com/show/3"),
        ("?page=2", "https://example.com/intl-de/x?page=2"),
    ],
)
def test_relative_locations_are_joined(http, location, final):
    http.get("https://example.com/intl-de/x", status_code=302, headers={"Location": location})
    http.get(final, content=b"done")
    result = make().get("https://example.com/intl-de/x", max_bytes=100)
    assert result.url == final
    assert result.body == b"done"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_all_redirect_statuses_are_followed(http, status):
    http.get("https://example.com/a", status_code=status, headers={"Location": "/b"})
    http.get("https://example.com/b", content=b"b")
    assert make().get("https://example.com/a", max_bytes=10).body == b"b"


def test_303_keeps_head_requests_as_head(http):
    http.head("https://example.com/a", status_code=303, headers={"Location": "/b"})
    http.head("https://example.com/b", headers={"X-Final": "yes"})
    result = make().get("https://example.com/a", max_bytes=10, method="HEAD")
    assert result.headers["x-final"] == "yes"
    assert [r.method for r in http.request_history] == ["HEAD", "HEAD"]


def test_redirect_without_location_is_returned(http):
    http.get("https://example.com/a", status_code=302, content=b"nowhere")
    result = make().get("https://example.com/a", max_bytes=100)
    assert (result.status, result.body, result.location) == (302, b"nowhere", None)


def test_every_redirect_hop_is_checked_against_the_allowlist(http):
    http.get(
        "https://example.com/a", status_code=302, headers={"Location": "https://example.org/b"}
    )
    with pytest.raises(FetchError) as info:
        make(allow=["example.com"]).get("https://example.com/a", max_bytes=10)
    assert info.value.code == "host_not_allowed"
    assert http.call_count == 1


def test_redirect_to_http_is_refused(http):
    http.get("https://example.com/a", status_code=301, headers={"Location": "http://example.com/b"})
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/a", max_bytes=10)
    assert info.value.code == "not_https"
    assert http.call_count == 1


def test_redirect_to_a_private_address_is_refused(http):
    http.get(
        "https://www.example.com/a",
        status_code=302,
        headers={"Location": "https://intranet.example.com/"},
    )
    resolver = Resolver(mapping={"intranet.example.com": [PRIVATE_V4]})
    with pytest.raises(FetchError) as info:
        make(resolver=resolver).get("https://www.example.com/a", max_bytes=10)
    assert info.value.code == "address_not_allowed"
    assert http.call_count == 1


def redirect_chain(http, hops):
    for number in range(hops):
        http.get(
            f"https://example.com/{number}", status_code=302, headers={"Location": f"/{number + 1}"}
        )
    http.get(f"https://example.com/{hops}", content=b"end")


def test_five_redirects_are_allowed(http):
    redirect_chain(http, 5)
    assert make().get("https://example.com/0", max_bytes=10).body == b"end"


def test_more_than_five_redirects_are_refused(http):
    redirect_chain(http, 6)
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/0", max_bytes=10)
    assert info.value.code == "too_many_redirects"
    assert http.call_count == 6


def test_redirect_loop_ends(http):
    http.get("https://example.com/loop", status_code=302, headers={"Location": "/loop"})
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/loop", max_bytes=10)
    assert info.value.code == "too_many_redirects"


def test_stop_redirect_returns_the_redirect_without_following_it(http):
    http.get(
        "https://example.com/s/abc", status_code=307, headers={"Location": "spotify:track:xyz"}
    )
    seen = []

    def stop(target):
        seen.append(target)
        return True

    result = make().get("https://example.com/s/abc", max_bytes=10, stop_redirect=stop)
    assert seen == ["spotify:track:xyz"]
    assert (result.url, result.status, result.location) == (
        "https://example.com/s/abc",
        307,
        "spotify:track:xyz",
    )
    assert result.body == b""
    assert http.call_count == 1


def test_stop_redirect_returning_false_keeps_following(http):
    http.get("https://example.com/a", status_code=301, headers={"Location": "/b"})
    http.get("https://example.com/b", status_code=301, headers={"Location": "/c"})
    http.get("https://example.com/c", content=b"c")
    seen = []

    def stop(target):
        seen.append(target)
        return target.endswith("/c")

    result = make().get("https://example.com/a", max_bytes=10, stop_redirect=stop)
    assert result.location == "https://example.com/c"
    assert result.url == "https://example.com/b"
    assert seen == ["https://example.com/b", "https://example.com/c"]


# -- size limits ---------------------------------------------------------------


def test_announced_size_above_the_limit_is_refused(http):
    http.get("https://example.com/", content=b"x" * 10, headers={"Content-Length": "5000"})
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/", max_bytes=4999)
    assert info.value.code == "too_large"


def test_streamed_size_above_the_limit_is_refused(http):
    http.get("https://example.com/", content=b"x" * 200_000)
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/", max_bytes=100_000)
    assert info.value.code == "too_large"


def test_body_of_exactly_the_limit_is_accepted(http):
    http.get("https://example.com/", content=b"x" * 70_000, headers={"Content-Length": "70000"})
    assert len(make().get("https://example.com/", max_bytes=70_000).body) == 70_000


def test_invalid_content_length_is_ignored(http):
    http.get("https://example.com/", content=b"abc", headers={"Content-Length": "lots"})
    assert make().get("https://example.com/", max_bytes=10).body == b"abc"


def test_truncate_returns_the_first_bytes(http):
    http.get("https://example.com/", content=b"x" * 200_000, headers={"Content-Length": "200000"})
    assert make().get("https://example.com/", max_bytes=1000, truncate=True).body == b"x" * 1000


def test_stop_at_ends_reading_after_the_marker(http):
    body = b"<html><head><title>t</title></HEAD><body>" + b"x" * 300_000
    http.get("https://example.com/", content=body)
    result = make().get("https://example.com/", max_bytes=1000, stop_at=b"</head>")
    assert result.body == b"<html><head><title>t</title></HEAD>"


def test_stop_at_finds_a_marker_across_chunk_boundaries(http):
    prefix = b"a" * (64 * 1024 - 3)
    body = prefix + b"</head>" + b"b" * 100_000
    http.get("https://example.com/", content=body)
    result = make().get("https://example.com/", max_bytes=200_000, stop_at=b"</head>")
    assert result.body == prefix + b"</head>"


def test_stop_at_without_marker_reads_everything(http):
    http.get("https://example.com/", content=b"no head here")
    assert make().get("https://example.com/", max_bytes=100, stop_at=b"</head>").body == (
        b"no head here"
    )


def test_marker_beyond_the_limit_is_too_large_or_truncated(http):
    body = b"a" * 500 + b"</head>"
    http.get("https://example.com/", content=body)
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/", max_bytes=100, stop_at=b"</head>")
    assert info.value.code == "too_large"
    truncated = make().get("https://example.com/", max_bytes=100, stop_at=b"</head>", truncate=True)
    assert truncated.body == b"a" * 100


# -- timeouts and transport errors -----------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (requests.exceptions.ConnectTimeout, "timeout"),
        (requests.exceptions.ReadTimeout, "timeout"),
        (requests.exceptions.ConnectionError, "connection_failed"),
        (requests.exceptions.SSLError, "connection_failed"),
        (requests.exceptions.InvalidURL, "connection_failed"),
    ],
)
def test_transport_errors_become_fetch_errors(http, exc, code):
    http.get("https://example.com/", exc=exc)
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/", max_bytes=10)
    assert info.value.code == code


@pytest.mark.parametrize(
    ("exc", "code"),
    [(TimeoutError("slow"), "timeout"), (OSError("reset"), "connection_failed")],
)
def test_errors_while_reading_the_body_become_fetch_errors(http, exc, code):
    http.get("https://example.com/", body=Breaking(exc))
    with pytest.raises(FetchError) as info:
        make().get("https://example.com/", max_bytes=100)
    assert info.value.code == code


def test_requests_timeout_while_reading_is_a_timeout():
    def iter_content(size):
        raise requests.exceptions.ReadTimeout("slow")

    response = SimpleNamespace(headers={}, iter_content=iter_content)
    with pytest.raises(FetchError) as info:
        make()._read(response, 100, None, False, 0.0)
    assert info.value.code == "timeout"


def test_timeouts_are_clipped_to_the_remaining_deadline(http):
    http.get("https://example.com/", content=b"ok")
    make(clock=Clock(0.0, 7.0, 7.5)).get("https://example.com/", max_bytes=10)
    assert http.last_request.timeout == (3.0, 3.0)


def test_deadline_covers_all_redirect_hops(http):
    http.get("https://example.com/a", status_code=302, headers={"Location": "/b"})
    http.get("https://example.com/b", content=b"late")
    with pytest.raises(FetchError) as info:
        make(clock=Clock(0.0, 6.0, 12.0)).get("https://example.com/a", max_bytes=10)
    assert info.value.code == "timeout"
    assert http.call_count == 1


def test_deadline_is_enforced_while_streaming(http):
    http.get("https://example.com/", content=b"x" * 200_000)
    with pytest.raises(FetchError) as info:
        make(clock=Clock(0.0, 4.0, 8.0, 12.0)).get("https://example.com/", max_bytes=1_000_000)
    assert info.value.code == "timeout"


def test_custom_deadline_and_timeouts(http):
    http.get("https://example.com/", content=b"ok")
    fetcher = make(timeout=(1.0, 2.0), deadline=30.0, clock=Clock(0.0, 25.0))
    fetcher.get("https://example.com/", max_bytes=10)
    assert http.last_request.timeout == (1.0, 2.0)


# -- session hygiene -----------------------------------------------------------


def set_cookie_response(url):
    message = HTTPMessage()
    message["Set-Cookie"] = "session=abc; Path=/"
    return SimpleNamespace(_original_response=SimpleNamespace(msg=message))


@pytest.mark.parametrize("injected", [False, True])
def test_no_cookies_are_stored(injected):
    fetcher = make(session=requests.Session() if injected else None)
    request = requests.Request("GET", "https://example.com/").prepare()
    extract_cookies_to_jar(fetcher._session.cookies, request, set_cookie_response(request.url))
    assert list(fetcher._session.cookies) == []

    plain = requests.Session()
    extract_cookies_to_jar(plain.cookies, request, set_cookie_response(request.url))
    assert len(list(plain.cookies)) == 1  # the check above is meaningful


def test_default_session_ignores_environment_settings():
    fetcher = Fetcher()
    assert fetcher._session.trust_env is False


def test_injected_session_is_used_and_closed(http):
    session = requests.Session()
    fetcher = make(session=session)
    assert fetcher._session is session
    closed = []
    session.close = lambda: closed.append(True)
    fetcher.close()
    assert closed == [True]
