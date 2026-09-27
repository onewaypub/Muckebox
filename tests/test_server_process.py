# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end tests of the real server process (waitress), on loopback only."""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from muckebox.web.app import CSRF_HEADER, MAX_REQUEST_BYTES

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    env = {
        **os.environ,
        "SONOS_IP": "192.0.2.10",
        "DATA_DIR": str(tmp_path / "data"),
        "PORT": str(port),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "muckebox"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"server did not start:\n{process.communicate()[0]}")
            time.sleep(0.05)
    yield process, port
    if process.poll() is None:
        process.kill()
    process.communicate()  # reaps the process and closes the pipe


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    finally:
        connection.close()


def test_health_over_real_http(server):
    _, port = server
    status, content_type, body = request(port, "GET", "/api/health")
    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body)["ok"] is True


def test_oversized_body_gets_json_envelope(server):
    _, port = server
    status, content_type, body = request(
        port,
        "POST",
        "/api/health",
        body=b"x" * (MAX_REQUEST_BYTES + 1),
        headers={CSRF_HEADER: "1"},
    )
    assert status == 413
    assert content_type == "application/json"
    assert json.loads(body)["error"]["code"] == "request_too_large"


def test_sigterm_stops_cleanly(server):
    process, port = server
    assert request(port, "GET", "/api/health")[0] == 200
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 0
    assert "Shutting down" in output
    assert "Traceback" not in output
