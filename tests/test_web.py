# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest
from flask import Flask

from muckebox import __version__
from muckebox.config import load_settings
from muckebox.i18n import CATALOGUES, DEFAULT_LANG
from muckebox.web import create_app
from muckebox.web.app import CSRF_HEADER, MAX_REQUEST_BYTES
from muckebox.web.errors import ApiError


@pytest.fixture
def app() -> Flask:
    app = create_app(load_settings({"SONOS_IP": "192.0.2.10"}))

    # Test-only routes to exercise the error handling.
    @app.post("/api/_test/echo")
    def echo():
        return {"ok": True}

    @app.get("/api/_test/api-error")
    def api_error():
        raise ApiError(503, "sonos_unreachable", retry_in=8)

    @app.get("/api/_test/crash")
    def crash():
        raise RuntimeError("boom")

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "version": __version__}


def test_api_responses_are_not_cached(client):
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/health", "/does-not-exist"])
def test_security_headers(client, path):
    headers = client.get(path).headers
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src" not in csp or "'unsafe-inline'" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"


def test_mutation_without_csrf_header_is_rejected(client):
    response = client.post("/api/_test/echo")
    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "error": {"code": "csrf_header_missing"}}


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_mutation_with_wrong_csrf_header_is_rejected(client, value):
    assert client.post("/api/_test/echo", headers={CSRF_HEADER: value}).status_code == 403


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_all_unsafe_methods_need_the_header(client, method):
    assert getattr(client, method)("/api/_test/echo").status_code == 403


def test_mutation_with_csrf_header_passes(client):
    response = client.post("/api/_test/echo", headers={CSRF_HEADER: "1"})
    assert response.status_code == 200


def test_not_found_uses_error_envelope(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json() == {"ok": False, "error": {"code": "not_found"}}


def test_method_not_allowed_uses_error_envelope(client):
    response = client.get("/api/_test/echo")
    assert response.status_code == 405
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert "POST" in response.headers["Allow"]


def test_too_large_request_is_rejected(client):
    response = client.post(
        "/api/_test/echo",
        data=b"x" * (MAX_REQUEST_BYTES + 1),
        headers={CSRF_HEADER: "1"},
    )
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "request_too_large"


def test_api_error_carries_retry_hint(client):
    response = client.get("/api/_test/api-error")
    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "error": {"code": "sonos_unreachable", "retry_in": 8},
    }


def test_unexpected_exception_is_hidden_and_logged(client, caplog):
    response = client.get("/api/_test/crash")
    assert response.status_code == 500
    assert response.get_json() == {"ok": False, "error": {"code": "internal_error"}}
    assert "boom" not in response.get_data(as_text=True)
    assert "Unhandled error" in caplog.text


@pytest.mark.parametrize(
    "code",
    ["bad_request", "csrf_header_missing", "not_found", "method_not_allowed",
     "request_too_large", "internal_error"],
)  # fmt: skip
def test_generic_error_codes_have_texts(code):
    assert f"error.{code}" in CATALOGUES[DEFAULT_LANG]


def test_create_app_starts_no_threads():
    import threading

    before = threading.active_count()
    create_app(load_settings({"SONOS_IP": "192.0.2.10"}))
    assert threading.active_count() == before
