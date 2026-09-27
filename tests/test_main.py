# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import errno
import logging
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
from muckebox.config import LISTEN_ALL


@pytest.fixture
def env(tmp_path):
    return {
        "SONOS_IP": "192.0.2.10",
        "DATA_DIR": str(tmp_path / "data"),
        "ADMIN_PIN": "2468",
        "MUCKEBOX_FAKE_SONOS": "1",
    }


def test_main_serves_app_on_configured_port(env):
    served = []
    assert (
        main({**env, "PORT": "9123"}, serve=lambda app, port, host: served.append((port, host)))
        == EXIT_OK
    )
    assert served == [(9123, LISTEN_ALL)]


def test_main_creates_data_dir(env, tmp_path):
    main(env, serve=lambda app, port, host: None)
    assert (tmp_path / "data").is_dir()
    assert not list((tmp_path / "data").glob(".write-test-*"))  # write test leaves nothing


def test_secret_key_is_created_once_and_private(env, tmp_path):
    main(env, serve=lambda app, port, host: None)
    key_file = tmp_path / "data" / "secret_key"
    key = key_file.read_bytes()
    assert len(key) == 32
    assert key_file.stat().st_mode & 0o077 == 0
    main(env, serve=lambda app, port, host: None)
    assert key_file.read_bytes() == key


def test_invalid_sonos_settings_still_serve(env):
    served = []
    code = main(
        {**env, "SONOS_IP": "", "MUCKEBOX_FAKE_SONOS": ""}, serve=lambda a, p, h: served.append(a)
    )
    assert code == EXIT_OK
    assert served


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
    def busy(app, port, host):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    assert main(env, serve=busy) == EXIT_PORT_IN_USE
    assert "already in use" in caplog.text


def test_other_bind_errors_are_reported_cleanly(env, caplog):
    def broken(app, port, host):
        raise OSError(errno.EACCES, "Permission denied")

    assert main(env, serve=broken) == EXIT_CONFIG
    assert "Permission denied" in caplog.text


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
def test_serve_closes_server_on_shutdown(monkeypatch, exc, tmp_path):
    server = FakeServer(exc)
    options = {}

    def fake_create_server(app, **kwargs):
        options.update(kwargs)
        return server

    monkeypatch.setattr(entry, "create_server", fake_create_server)
    installed = {}

    def fake_signal(signum, handler):
        installed[signum] = handler

    monkeypatch.setattr(entry.signal, "signal", fake_signal)
    settings = entry.load_settings({"SONOS_IP": "192.0.2.10", "DATA_DIR": str(tmp_path)})
    app = entry.create_app(entry.build_services(settings, fake_sonos=True))
    entry._serve(app, 9123, "127.0.0.1")
    # After the first signal, a second Ctrl+C or SIGTERM ends the process at once.
    assert installed == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}
    assert server.closed
    assert (options["port"], options["host"]) == (9123, "127.0.0.1")
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


def test_listen_address_is_logged_and_used(env, caplog):
    caplog.set_level(logging.INFO)
    served = []
    main({**env, "LISTEN": "localhost"}, serve=lambda app, port, host: served.append(host))
    assert served == ["127.0.0.1"]
    assert "this computer only" in caplog.text


def test_foreign_listen_address_is_a_config_error(env, caplog):
    def not_here(app, port, host):
        raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")

    assert main({**env, "LISTEN": "192.0.2.99"}, serve=not_here) == EXIT_CONFIG
    assert "Cannot listen on 192.0.2.99" in caplog.text


def test_second_interrupt_during_shutdown_exits_quietly(env, monkeypatch, caplog):
    from muckebox.runtime.service import Runtime

    caplog.set_level(logging.INFO)

    def interrupted(self, timeout=2.0):
        raise KeyboardInterrupt

    monkeypatch.setattr(Runtime, "stop", interrupted)
    assert main(env, serve=lambda app, port, host: None) == EXIT_OK
    assert "without waiting" in caplog.text


def test_settings_from_a_newer_version_stop_the_start(env, tmp_path, caplog):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text('{"schema": 99}')
    served = []
    assert main(env, serve=lambda *a: served.append(a)) == EXIT_CONFIG
    assert not served
    assert "newer" in caplog.text
    assert (data / "settings.json").read_text() == '{"schema": 99}'
