# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point: ``python -m muckebox``."""

from __future__ import annotations

import errno
import logging
import os
import signal
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from types import FrameType

from flask import Flask
from waitress import create_server

from muckebox import __version__
from muckebox.config import FatalConfigError, load_settings
from muckebox.web import create_app

log = logging.getLogger("muckebox")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DATA_DIR = 3
EXIT_PORT_IN_USE = 4

WEB_THREADS = 8


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

    app = create_app(settings)
    log.info("Muckebox %s listening on port %d", __version__, settings.port)
    try:
        serve(app, settings.port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            log.error("Port %d is already in use on this host; choose another PORT.", settings.port)
            return EXIT_PORT_IN_USE
        raise
    return EXIT_OK


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


class _Shutdown(Exception):
    """Raised by the SIGTERM handler to stop the server loop."""


def _raise_shutdown(signum: int, frame: FrameType | None) -> None:
    raise _Shutdown


def _serve(app: Flask, port: int) -> None:
    server = create_server(
        app,
        # IPv4 on all interfaces: tablets on the home network connect to the
        # host's LAN address. Muckebox must not be exposed to the internet.
        host="0.0.0.0",  # noqa: S104
        port=port,
        threads=WEB_THREADS,
        ident="Muckebox",
        max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
    )
    signal.signal(signal.SIGTERM, _raise_shutdown)
    try:
        server.run()
    except (KeyboardInterrupt, _Shutdown):
        log.info("Shutting down")
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main())
