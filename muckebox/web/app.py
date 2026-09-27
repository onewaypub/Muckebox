# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flask application factory.

``create_app`` only wires up the HTTP layer. It never starts threads or
talks to the network, so tests can create as many apps as they like.
"""

from __future__ import annotations

from flask import Flask, Response, current_app, request
from werkzeug.exceptions import RequestEntityTooLarge

from muckebox.config import Settings

from . import api
from .errors import ApiError, register_error_handlers

# Header that every state-changing request must carry. Browsers cannot send
# a custom header cross-site without a CORS preflight (which Muckebox never
# allows), so this blocks cross-site request forgery against the kids API.
CSRF_HEADER = "X-Muckebox"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

MAX_REQUEST_BYTES = 1024 * 1024

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)


def create_app(settings: Settings) -> Flask:
    app = Flask("muckebox", static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.extensions["muckebox.settings"] = settings

    register_error_handlers(app)
    app.before_request(_limit_request_size)
    app.before_request(_require_csrf_header)
    app.after_request(_add_security_headers)
    app.register_blueprint(api.bp)
    return app


def _limit_request_size() -> None:
    # Reject early with the JSON envelope, even if a view never reads the
    # body. Waitress buffers bodies and rejects grossly oversized ones itself
    # (see WAITRESS_BODY_LIMIT_FACTOR in __main__).
    limit = current_app.config["MAX_CONTENT_LENGTH"]
    if request.content_length is not None and request.content_length > limit:
        raise RequestEntityTooLarge()


def _require_csrf_header() -> None:
    if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise ApiError(403, "csrf_header_missing")


def _add_security_headers(response: Response) -> Response:
    headers = response.headers
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("X-Frame-Options", "DENY")
    if request.path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"
    return response
