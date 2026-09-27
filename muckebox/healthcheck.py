# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Docker health check: ``python -m muckebox.healthcheck``.

Asks the running server for /api/health on the address it listens on
(``LISTEN``), so the check also works when Muckebox listens on one address.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from collections.abc import Mapping

from muckebox.config import DEFAULT_PORT, LISTEN_ALL, LISTEN_LOCALHOST, FatalConfigError
from muckebox.config import _parse_listen as parse_listen


def health_url(environ: Mapping[str, str]) -> str:
    host = parse_listen(environ.get("LISTEN", "").strip() or None)
    if host == LISTEN_ALL:
        host = LISTEN_LOCALHOST
    port = environ.get("PORT", "").strip() or str(DEFAULT_PORT)
    return f"http://{host}:{port}/api/health"


def main(environ: Mapping[str, str] = os.environ) -> int:
    try:
        url = health_url(environ)
        # The URL is always http://<own address>:<port>/api/health.
        with urllib.request.urlopen(url, timeout=4) as response:  # noqa: S310  # nosec B310
            return 0 if response.status == 200 else 1
    except (FatalConfigError, OSError, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
