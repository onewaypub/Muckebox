# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crash-safe file writes in the data directory."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: bytes, *, mode: int = 0o644, backup: bool = False) -> None:
    """Replace ``path`` with ``data`` so that readers see the old or the new file.

    The data goes to a uniquely named temporary file in the same directory,
    is flushed to disk and then renamed over ``path``. With ``backup`` the
    previous file is kept as ``<name>.bak``. ``mode`` is applied before any
    data is written (a failing chmod, e.g. on ACL-managed shares, is ignored).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with contextlib.suppress(OSError):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
