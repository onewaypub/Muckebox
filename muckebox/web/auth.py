# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""PIN login for the parents' page.

* Without ``ADMIN_PIN`` the parents' page is locked.
* Failed logins are rate limited per client and globally.
* The session cookie is bound to the current PIN: changing ``ADMIN_PIN``
  ends all sessions.
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

from .errors import ApiError

SESSION_LIFETIME = timedelta(hours=12)
FAILURE_WINDOW = 15 * 60  # seconds
MAX_FAILURES_PER_CLIENT = 5
MAX_FAILURES_GLOBAL = 20


class RateLimiter:
    """Counts failed logins in memory; entries older than the window are dropped."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._per_client: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def retry_in(self, client: str) -> int | None:
        """Seconds until ``client`` may try again, or None if it may now."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            waits = [
                self._wait(self._per_client.get(client, deque()), MAX_FAILURES_PER_CLIENT, now),
                self._wait(self._global, MAX_FAILURES_GLOBAL, now),
            ]
        known = [wait for wait in waits if wait is not None]
        return max(known) if known else None

    def failure(self, client: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._per_client.setdefault(client, deque()).append(now)
            self._global.append(now)

    def success(self, client: str) -> None:
        with self._lock:
            self._per_client.pop(client, None)

    def _prune(self, now: float) -> None:
        for queue in (*self._per_client.values(), self._global):
            while queue and now - queue[0] > FAILURE_WINDOW:
                queue.popleft()
        for client in [c for c, queue in self._per_client.items() if not queue]:
            del self._per_client[client]

    @staticmethod
    def _wait(failures: deque[float], limit: int, now: float) -> int | None:
        if len(failures) < limit:
            return None
        return int(FAILURE_WINDOW - (now - failures[0])) + 1


def _pin() -> str | None:
    return current_app.extensions["muckebox"].settings.admin_pin


def _token(pin: str) -> str:
    key = current_app.config["SECRET_KEY"]
    return hmac.new(key, pin.encode(), hashlib.sha256).hexdigest()


def is_locked() -> bool:
    return _pin() is None


def is_logged_in() -> bool:
    pin = _pin()
    token = session.get("auth")
    expires = session.get("exp", 0)
    return (
        pin is not None
        and isinstance(token, str)
        and hmac.compare_digest(token, _token(pin))
        and time.time() < expires
    )


def login(given: str, limiter: RateLimiter) -> None:
    pin = _pin()
    if pin is None:
        raise ApiError(403, "admin_locked")
    client = request.remote_addr or "unknown"
    wait = limiter.retry_in(client)
    if wait is not None:
        raise ApiError(429, "pin_rate_limited", wait)
    if not hmac.compare_digest(given.encode(), pin.encode()):
        limiter.failure(client)
        raise ApiError(401, "pin_wrong")
    limiter.success(client)
    session.clear()
    session.permanent = True
    session["auth"] = _token(pin)
    session["exp"] = time.time() + SESSION_LIFETIME.total_seconds()


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
        if is_locked():
            raise ApiError(403, "admin_locked")
        if not is_logged_in():
            raise ApiError(401, "login_required")
        if request.method not in ("GET", "HEAD") and not _same_origin():
            raise ApiError(403, "origin_mismatch")
        return view(*args, **kwargs)

    return wrapper
