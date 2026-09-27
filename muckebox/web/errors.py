# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON error envelope shared by all endpoints.

Errors are reported as ``{"ok": false, "error": {"code": ..., "retry_in": ...}}``.
``code`` is a stable key; clients translate ``error.<code>`` with the i18n
catalogue.
"""

from __future__ import annotations

import logging

from flask import Flask, Response, jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "request_too_large",
}


class ApiError(Exception):
    """Raise from a view to return a JSON error response."""

    def __init__(self, status: int, code: str, retry_in: int | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_in = retry_in


def error_response(status: int, code: str, retry_in: int | None = None) -> tuple[Response, int]:
    error: dict[str, object] = {"code": code}
    if retry_in is not None:
        error["retry_in"] = retry_in
    return jsonify(ok=False, error=error), status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return error_response(exc.status, exc.code, exc.retry_in)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        status = exc.code or 500
        default = "bad_request" if status < 500 else "internal_error"
        response, status = error_response(status, _HTTP_CODES.get(status, default))
        if status == 405 and exc.get_headers():
            allow = dict(exc.get_headers()).get("Allow")
            if allow:
                response.headers["Allow"] = allow
        return response, status

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        log.exception("Unhandled error while processing a request")
        return error_response(500, "internal_error")
