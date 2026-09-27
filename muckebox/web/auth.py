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
    """Counts failed logins in memory; entries older than the window are dropped."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._per_client: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._pin_version: str | None = None

    def follow_pin(self, version: str) -> None:
        """Forget all failures when the PIN has changed since the last call."""
        with self._lock:
            if version != self._pin_version:
                self._pin_version = version
                self._per_client.clear()
                self._global.clear()

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


def check_pin(given: object, limiter: RateLimiter) -> None:
    """Raise unless ``given`` is the current PIN (rate limited)."""
    store = _store()
    limiter.follow_pin(store.current().pin.version)
    client = request.remote_addr or "unknown"
    wait = limiter.retry_in(client)
    if wait is not None:
        raise ApiError(429, "pin_rate_limited", wait)
    valid = isinstance(given, str) and len(given) <= MAX_PIN_LENGTH and store.verify_pin(given)
    if not valid:
        limiter.failure(client)
        raise ApiError(401, "pin_wrong")
    limiter.success(client)


def start_session() -> None:
    """Log this browser in with the current PIN (ends nothing else)."""
    session.clear()
    session.permanent = True
    session["auth"] = _token(_store().current().pin.version)
    session["exp"] = time.time() + SESSION_LIFETIME.total_seconds()


def login(given: object, limiter: RateLimiter) -> None:
    check_pin(given, limiter)
    start_session()


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
