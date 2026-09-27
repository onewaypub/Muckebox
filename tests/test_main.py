# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import errno
import os
import signal

import pytest

import muckebox.__main__ as entry
from muckebox.__main__ import (
    EXIT_CONFIG,
    EXIT_DATA_DIR,
    EXIT_OK,
    EXIT_PORT_IN_USE,
    DataDirError,
    ensure_data_dir,
    main,
)


@pytest.fixture
def env(tmp_path):
    return {"SONOS_IP": "192.0.2.10", "DATA_DIR": str(tmp_path / "data"), "ADMIN_PIN": "2468"}


def test_main_serves_app_on_configured_port(env):
    served = []
    assert main({**env, "PORT": "9123"}, serve=lambda app, port: served.append(port)) == EXIT_OK
    assert served == [9123]


def test_main_creates_data_dir(env, tmp_path):
    main(env, serve=lambda app, port: None)
    assert (tmp_path / "data").is_dir()
    assert list((tmp_path / "data").iterdir()) == []  # write test leaves nothing behind


def test_invalid_port_exits_before_serving(env, caplog):
    served = []
    assert main({**env, "PORT": "1400"}, serve=lambda *a: served.append(a)) == EXIT_CONFIG
    assert not served
    assert "PORT" in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_unwritable_data_dir_exits_with_hint(env, tmp_path, caplog):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    code = main({**env, "DATA_DIR": str(locked)}, serve=lambda *a: None)
    assert code == EXIT_DATA_DIR
    assert f"uid {os.getuid()}" in caplog.text
    assert "docker-compose.yml" in caplog.text


def test_ensure_data_dir_reports_os_errors(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(DataDirError):
        ensure_data_dir(blocker / "data")


def test_port_in_use_exits_with_clear_message(env, caplog):
    def busy(app, port):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    assert main(env, serve=busy) == EXIT_PORT_IN_USE
    assert "already in use" in caplog.text


def test_other_os_errors_propagate(env):
    def broken(app, port):
        raise OSError(errno.EACCES, "Permission denied")

    with pytest.raises(OSError):
        main(env, serve=broken)


def test_config_problems_are_logged_without_secrets(env, caplog):
    main({**env, "ADMIN_PIN": "12", "MAX_VOLUME": "500"}, serve=lambda *a: None)
    assert "MAX_VOLUME" in caplog.text
    assert "ADMIN_PIN must have at least" in caplog.text
    assert "'12'" not in caplog.text


class FakeServer:
    def __init__(self, exc):
        self.exc = exc
        self.closed = False

    def run(self):
        raise self.exc

    def close(self):
        self.closed = True


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), entry._Shutdown()])
def test_serve_closes_server_on_shutdown(monkeypatch, exc):
    server = FakeServer(exc)
    options = {}

    def fake_create_server(app, **kwargs):
        options.update(kwargs)
        return server

    monkeypatch.setattr(entry, "create_server", fake_create_server)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        app = entry.create_app(entry.load_settings({"SONOS_IP": "192.0.2.10"}))
        entry._serve(app, 9123)
        assert signal.getsignal(signal.SIGTERM) is entry._raise_shutdown
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert server.closed
    assert options["port"] == 9123
    assert options["max_request_body_size"] > app.config["MAX_CONTENT_LENGTH"]


def test_sigterm_handler_raises_shutdown_once():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(entry._Shutdown) as info:
            entry._raise_shutdown(signal.SIGTERM, None)
        assert info.value.code == 0
        # A second SIGTERM must terminate immediately instead of raising again.
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_shutdown_is_a_system_exit():
    # waitress only drains its worker threads (and re-raises from channel
    # handlers) for SystemExit and KeyboardInterrupt.
    assert issubclass(entry._Shutdown, SystemExit)
