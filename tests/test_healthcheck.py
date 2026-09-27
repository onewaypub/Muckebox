# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox import healthcheck


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ({}, "http://127.0.0.1:8484/api/health"),
        ({"LISTEN": "all", "PORT": "9000"}, "http://127.0.0.1:9000/api/health"),
        ({"LISTEN": "localhost"}, "http://127.0.0.1:8484/api/health"),
        ({"LISTEN": "192.0.2.5"}, "http://192.0.2.5:8484/api/health"),
    ],
)
def test_health_url_follows_listen(env, url):
    assert healthcheck.health_url(env) == url


def test_invalid_listen_is_unhealthy():
    assert healthcheck.main({"LISTEN": "nowhere"}) == 1


def test_healthy_and_unhealthy(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    urls = []
    monkeypatch.setattr(
        healthcheck.urllib.request, "urlopen", lambda url, timeout: urls.append(url) or Response()
    )
    assert healthcheck.main({"LISTEN": "192.0.2.5", "PORT": "9001"}) == 0
    assert urls == ["http://192.0.2.5:9001/api/health"]

    def refused(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", refused)
    assert healthcheck.main({}) == 1
