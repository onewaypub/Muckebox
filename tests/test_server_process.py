# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end tests of the real server process (waitress), on loopback only."""

import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from muckebox.web.app import CSRF_HEADER, MAX_REQUEST_BYTES

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Output:
    """Collects the server's log while it runs."""

    def __init__(self, stream):
        self.lines = []
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream):
        with stream:  # closed at the end, when the server has exited
            for line in stream:
                self.lines.append(line)

    def text(self):
        return "".join(self.lines)


@pytest.fixture
def server(tmp_path):
    port = free_port()
    env = {
        **os.environ,
        "MUCKEBOX_FAKE_SONOS": "1",
        "DATA_DIR": str(tmp_path / "data"),
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "muckebox"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = Output(process.stdout)
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                process.wait()
                output.thread.join(5)
                pytest.fail(f"server did not start:\n{output.text()}")
            time.sleep(0.05)
    process.output = output
    process.env = env
    yield process, port
    if process.poll() is None:
        process.kill()
    process.wait()
    output.thread.join(5)


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
    assert process.wait(timeout=10) == 0
    process.output.thread.join(5)
    output = process.output.text()
    assert "Shutting down" in output
    assert "Traceback" not in output


class Browser:
    """Just enough of a browser for the parents' page: one cookie, the CSRF header."""

    def __init__(self, port):
        self.port = port
        self.cookie = None

    def call(self, method, path, body=None):
        headers = {CSRF_HEADER: "1", "Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            data = json.dumps(body) if body is not None else None
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def login(self, pin):
        return self.call("POST", "/api/admin/login", {"pin": pin})[0]


def wait_for(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


def test_first_start_and_pin_reset(server):
    process, port = server
    wait_for(lambda: "Parents' PIN" in process.output.text())
    pin = re.search(r"Parents' PIN: (\d{6})", process.output.text()).group(1)
    browser = Browser(port)
    assert browser.login(pin) == 200
    status, data = browser.call("POST", "/api/admin/rooms/search", {})
    assert status == 200
    assert [room["name"] for room in data["rooms"]] == ["Kinderzimmer", "Wohnzimmer"]
    status, data = browser.call("PUT", "/api/admin/settings/room", {"room": "Kinderzimmer"})
    assert (status, data["sonos"]["status"]) == (200, "ok")

    reset = subprocess.run(
        [sys.executable, "-m", "muckebox.admin", "reset-pin"],
        cwd=ROOT,
        env=process.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    new_pin = re.search(r"PIN for the parents' page: (\d{6})", reset.stdout).group(1)
    # The running server notices the new PIN: the old session ends.
    wait_for(lambda: browser.call("GET", "/api/admin/settings")[0] == 401)
    assert Browser(port).login(pin) == 401
    assert browser.login(new_pin) == 200
    status, data = browser.call("GET", "/api/admin/settings")
    assert data["settings"]["room"] == "Kinderzimmer"  # only the PIN was reset
    assert data["settings"]["pin_generated"] is True
