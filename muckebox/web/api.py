# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON API endpoints for the kids view and other devices."""

from __future__ import annotations

from flask import Blueprint, jsonify

from muckebox import __version__

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/health")
def health():
    """Liveness of the web server; independent of the Sonos connection."""
    return jsonify(ok=True, version=__version__)
