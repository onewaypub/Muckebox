# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Administration from the command line, e.g. inside the container::

    docker exec -it muckebox python -m muckebox.admin reset-pin

reset-pin sets a new random PIN for the parents' page, prints it and logs
out all devices. A running Muckebox notices the new PIN within a second and
prints it in its log on every start until the parents set their own.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from muckebox.config import data_dir_from
from muckebox.settings import SettingsFileError, SettingsStore

EXIT_OK = 0
EXIT_SETTINGS = 2
EXIT_NOT_WRITABLE = 3


def reset_pin(environ: Mapping[str, str], out: TextIO) -> int:
    data_dir = data_dir_from(environ)
    try:
        store = SettingsStore(data_dir)
        pin = store.reset_pin()
    except SettingsFileError as exc:
        out.write(f"Error: {exc}\n")
        return EXIT_SETTINGS
    except OSError as exc:
        out.write(
            f"Error: cannot write the settings in {data_dir} ({exc.strerror or exc}). "
            "Run this as the user Muckebox runs as, or as root.\n"
        )
        return EXIT_NOT_WRITABLE
    if store.load_problem:
        out.write(
            "Warning: settings.json could not be read and was moved aside. "
            "Choose the room and the volume limit again on the parents' page.\n"
        )
    out.write(f"New PIN for the parents' page: {pin}\n")
    out.write("All devices are logged out. Log in with this PIN and then set your own PIN.\n")
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m muckebox.admin",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset-pin", help="set a new random PIN and print it")
    parser.parse_args(argv)
    return reset_pin(environ, out)


if __name__ == "__main__":
    sys.exit(main())
