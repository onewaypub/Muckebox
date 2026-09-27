# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PIN login for the parents' page.

* The PIN is kept as a scrypt hash in ``settings.json``.
* Failed logins are rate limited per client and globally. A new PIN (set on
  the parents' page or with ``reset-pin``) clears the counters.
* The session cookie is bound to the current PIN: any PIN change ends all
  sessions.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from flask import current_app, request, session

from muckebox.settings import MAX_PIN_LENGTH, SettingsStore

from .errors import ApiError

SESSION_LIFETIME = timedelta(hours=12)
FAILURE_WINDOW = 15 * 60  # seconds
MAX_FAILURES_PER_CLIENT = 5
MAX_FAILURES_GLOBAL = 20


class RateLimiter:
    """Counts failed logins in memory; entries older than the window are dropped.

    An attempt is reserved before the (slow) PIN check starts, so parallel
    requests cannot get more guesses than the limits allow.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        share_global_with: RateLimiter | None = None,
    ) -> None:
        self._clock = clock
        self._per_client: dict[str, deque[float]] = {}
        if share_global_with is None:
            self._lock = threading.Lock()
            self._global: deque[float] = deque()
        else:
            # Own per-client counts, one total for both: two ways to check the
            # PIN must not double the guesses an attacker gets.
            self._lock = share_global_with._lock
            self._global = share_global_with._global
        self._running: dict[str, int] = {}  # checks in progress per client
        self._pin_version: str | None = None

    def follow_pin(self, version: str) -> None:
        """Forget all failures when the PIN has changed since the last call."""
        with self._lock:
            if version != self._pin_version:
                self._pin_version = version
                self._per_client.clear()
                self._global.clear()

    def begin(self, client: str) -> int | None:
        """Reserve one attempt. Returns the seconds to wait if ``client`` may not try now."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            running = self._running.get(client, 0)
            waits = [
                self._wait(
                    self._per_client.get(client, deque()), running, MAX_FAILURES_PER_CLIENT, now
                ),
                self._wait(self._global, sum(self._running.values()), MAX_FAILURES_GLOBAL, now),
            ]
            known = [wait for wait in waits if wait is not None]
            if known:
                return max(known)
            self._running[client] = running + 1
            return None

    def finish(self, client: str, ok: bool) -> None:
        """End an attempt reserved with :meth:`begin`."""
        with self._lock:
            self._running[client] -= 1
            if not self._running[client]:
                del self._running[client]
            if ok:
                self._per_client.pop(client, None)
            else:
                now = self._clock()
                self._per_client.setdefault(client, deque()).append(now)
                self._global.append(now)

    def _prune(self, now: float) -> None:
        for queue in (*self._per_client.values(), self._global):
            while queue and now - queue[0] > FAILURE_WINDOW:
                queue.popleft()
        for client in [c for c, queue in self._per_client.items() if not queue]:
            del self._per_client[client]

    @staticmethod
    def _wait(failures: deque[float], running: int, limit: int, now: float) -> int | None:
        if len(failures) >= limit:
            return int(FAILURE_WINDOW - (now - failures[0])) + 1
        if len(failures) + running >= limit:
            return 1  # the checks still running may use up the remaining attempts
        return None


def _store() -> SettingsStore:
    return current_app.extensions["muckebox"].store


def _token(pin_version: str) -> str:
    key = current_app.config["SECRET_KEY"]
    return hmac.new(key, pin_version.encode(), hashlib.sha256).hexdigest()


def is_logged_in() -> bool:
    token = session.get("auth")
    expires = session.get("exp", 0)
    return (
        isinstance(token, str)
        and hmac.compare_digest(token, _token(_store().current().pin.version))
        and time.time() < expires
    )


def check_pin(given: object, limiter: RateLimiter) -> str:
    """Return the version of the PIN that ``given`` matched; raise otherwise (rate limited)."""
    store = _store()
    limiter.follow_pin(store.current().pin.version)
    client = request.remote_addr or "unknown"
    wait = limiter.begin(client)
    if wait is not None:
        raise ApiError(429, "pin_rate_limited", wait)
    version = None
    try:
        if isinstance(given, str) and len(given) <= MAX_PIN_LENGTH:
            version = store.check(given)
    finally:
        limiter.finish(client, ok=version is not None)
    if version is None:
        raise ApiError(401, "pin_wrong")
    return version


def start_session(pin_version: str) -> None:
    """Log this browser in, bound to ``pin_version`` (ends nothing else).

    Binding to the PIN that was actually checked matters: if the PIN changed
    while the check ran, this session must be invalid right away.
    """
    session.clear()
    session.permanent = True
    session["auth"] = _token(pin_version)
    session["exp"] = time.time() + SESSION_LIFETIME.total_seconds()


def login(given: object, limiter: RateLimiter) -> None:
    start_session(check_pin(given, limiter))


def logout() -> None:
    session.clear()


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # same-origin requests from older browsers may omit it
    host = urlsplit(request.host_url)
    sent = urlsplit(origin)
    return (sent.scheme, sent.netloc) == (host.scheme, host.netloc)


def require_admin(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not is_logged_in():
            raise ApiError(401, "login_required")
        if request.method not in ("GET", "HEAD") and not _same_origin():
            raise ApiError(403, "origin_mismatch")
        return view(*args, **kwargs)

    return wrapper


#: Failed PINs on the parents' page (login, PIN change) ...
login_limiter = RateLimiter()
#: ... and on the kids tablet's PIN pad: counted per client separately (so a
#: kid mashing the pad does not lock the parents out), but sharing one total.
override_limiter = RateLimiter(share_global_with=login_limiter)
