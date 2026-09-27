# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point: ``python -m muckebox``."""

from __future__ import annotations

import errno
import logging
import os
import secrets
import signal
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from types import FrameType

from flask import Flask
from waitress import create_server

from muckebox import __version__
from muckebox.config import FatalConfigError, Settings, load_settings
from muckebox.covers import CoverStore
from muckebox.library import Library
from muckebox.runtime.service import Runtime
from muckebox.sonos.backend import SonosBackend
from muckebox.web import create_app
from muckebox.web.app import Services

log = logging.getLogger("muckebox")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DATA_DIR = 3
EXIT_PORT_IN_USE = 4

WEB_THREADS = 8
WAITRESS_BODY_LIMIT_FACTOR = 2


class DataDirError(Exception):
    """DATA_DIR cannot be created or written."""


def main(
    environ: Mapping[str, str] = os.environ,
    serve: Callable[[Flask, int], None] | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    serve = serve or _serve

    try:
        settings = load_settings(environ)
    except FatalConfigError as exc:
        log.error("Invalid configuration: %s", exc)
        return EXIT_CONFIG

    try:
        ensure_data_dir(settings.data_dir)
    except DataDirError as exc:
        log.error("%s", exc)
        return EXIT_DATA_DIR

    for problem in settings.problems:
        level = logging.ERROR if problem.severity == "error" else logging.WARNING
        log.log(level, "Configuration: %s", problem.detail)

    services = build_services(settings, fake_sonos=environ.get("MUCKEBOX_FAKE_SONOS") == "1")
    app = create_app(services)
    services.runtime.start()
    log.info("Muckebox %s listening on port %d", __version__, settings.port)
    try:
        serve(app, settings.port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            log.error("Port %d is already in use on this host; choose another PORT.", settings.port)
            return EXIT_PORT_IN_USE
        raise
    finally:
        services.runtime.stop()
    return EXIT_OK


def build_services(settings: Settings, *, fake_sonos: bool = False) -> Services:
    backend: SonosBackend
    if not settings.sonos_config_ok and not fake_sonos:
        from muckebox.sonos.backend import UnconfiguredBackend

        backend = UnconfiguredBackend()  # type: ignore[assignment]
    elif fake_sonos:
        from muckebox.sonos.fake import FakeSonos

        log.warning("MUCKEBOX_FAKE_SONOS=1: using a simulated speaker (demo mode)")
        backend = FakeSonos()
    else:
        from muckebox.sonos.soco_backend import SocoBackend, configure_soco

        configure_soco()
        backend = SocoBackend(room=settings.sonos_room, seed_ip=settings.sonos_ip)
    library = Library(settings.data_dir / "library.json")
    covers = CoverStore(settings.data_dir / "covers")
    covers.delete_unused(library.covers_in_use())
    return Services(
        settings=settings,
        runtime=Runtime(settings, backend, library),
        library=library,
        covers=covers,
        secret_key=load_secret_key(settings.data_dir / "secret_key"),
    )


def load_secret_key(path: Path) -> bytes:
    """Read the session signing key, creating it on first start."""
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def ensure_data_dir(path: Path) -> None:
    """Create ``path`` if needed and check that it is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write-test-"):
            pass
    except OSError as exc:
        raise DataDirError(
            f"DATA_DIR {path} is not writable for this process "
            f"(uid {os.getuid()}, gid {os.getgid()}): {exc.strerror}. "
            "Create the folder on the host and give this user write access, "
            "or adjust 'user:' in docker-compose.yml."
        ) from exc


class _Shutdown(SystemExit):
    """Raised by the SIGTERM handler to stop the server loop.

    A SystemExit subclass: waitress re-raises it from its channel handlers
    and lets running requests finish before ``run()`` returns.
    """


def _raise_shutdown(signum: int, frame: FrameType | None) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)  # a second SIGTERM ends the process at once
    raise _Shutdown(0)


def _serve(app: Flask, port: int) -> None:
    server = create_server(
        app,
        # IPv4 on all interfaces: tablets on the home network connect to the
        # host's LAN address. Muckebox must not be exposed to the internet.
        host="0.0.0.0",  # noqa: S104  # nosec B104
        port=port,
        threads=WEB_THREADS,
        ident="Muckebox",
        # Backstop only: waitress answers oversized bodies with plain text.
        # Its cap is kept well above the app's limit so that Flask can reply
        # with the JSON error envelope for anything in between.
        max_request_body_size=WAITRESS_BODY_LIMIT_FACTOR * app.config["MAX_CONTENT_LENGTH"],
    )
    signal.signal(signal.SIGTERM, _raise_shutdown)
    try:
        server.run()
    except (KeyboardInterrupt, _Shutdown):
        pass  # only reached if the signal arrives outside waitress's loop
    finally:
        log.info("Shutting down")
        server.close()


if __name__ == "__main__":
    sys.exit(main())
