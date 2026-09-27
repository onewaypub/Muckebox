# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flask application factory.

``create_app`` only wires up the HTTP layer. It never starts threads or
talks to the network, so tests can create as many apps as they like.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, Response, current_app, request
from werkzeug.exceptions import RequestEntityTooLarge

from muckebox.config import Settings
from muckebox.covers import CoverStore
from muckebox.library import Library
from muckebox.runtime.service import Runtime

from . import admin, api, pages
from .errors import ApiError, register_error_handlers

PACKAGE_DIR = Path(__file__).resolve().parent.parent

# Header that every state-changing request must carry. Browsers cannot send
# a custom header cross-site without a CORS preflight (which Muckebox never
# allows), so this blocks cross-site request forgery against the kids API.
CSRF_HEADER = "X-Muckebox"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

MAX_REQUEST_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Multipart framing adds a little to the file itself.
_MAX_UPLOAD_REQUEST = MAX_UPLOAD_BYTES + 64 * 1024

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data: blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)


@dataclass
class Services:
    """Everything the HTTP layer needs, created once in ``__main__``."""

    settings: Settings
    runtime: Runtime
    library: Library
    covers: CoverStore
    secret_key: bytes


def create_app(services: Services) -> Flask:
    app = Flask(
        "muckebox",
        root_path=str(PACKAGE_DIR),
        static_folder="static",
        template_folder="templates",
    )
    app.config.update(
        MAX_CONTENT_LENGTH=_MAX_UPLOAD_REQUEST,
        SECRET_KEY=services.secret_key,
        SESSION_COOKIE_NAME="muckebox_admin",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SEND_FILE_MAX_AGE_DEFAULT=0,  # static files: always revalidate (cheap on a LAN)
    )
    app.json.sort_keys = False  # type: ignore[attr-defined]
    app.extensions["muckebox"] = services
    app.jinja_env.globals["asset_version"] = _asset_version(PACKAGE_DIR / "static")

    register_error_handlers(app)
    app.before_request(_limit_request_size)
    app.before_request(_require_csrf_header)
    app.after_request(_add_security_headers)
    app.register_blueprint(api.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(pages.bp)
    return app


def services() -> Services:
    return current_app.extensions["muckebox"]


def _asset_version(static_dir: Path) -> str:
    """A hash over all static files, used to bust browser caches on updates."""
    digest = hashlib.sha256()
    for path in sorted(static_dir.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(static_dir).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _limit_request_size() -> None:
    # Reject early with the JSON envelope, even if a view never reads the
    # body. Waitress buffers bodies and rejects grossly oversized ones itself
    # (see WAITRESS_BODY_LIMIT_FACTOR in __main__).
    limit = _MAX_UPLOAD_REQUEST if admin.is_upload_path(request.path) else MAX_REQUEST_BYTES
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
