# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON API for the kids view and other devices (no login)."""

from __future__ import annotations

import hashlib
import json

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from muckebox import __version__
from muckebox.library import TileNotFound
from muckebox.runtime.service import Busy, Unavailable

from .errors import ApiError

bp = Blueprint("api", __name__)


def _services():
    return current_app.extensions["muckebox"]


def cover_url(name: str | None) -> str | None:
    return f"/covers/{name}" if name else None


@bp.get("/api/health")
def health():
    """Liveness of the web server; independent of the Sonos connection."""
    return jsonify(ok=True, version=__version__)


@bp.get("/api/state")
def state():
    document = _services().runtime.state_document()
    body = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    etag = hashlib.sha256(body.encode()).hexdigest()[:16]
    if etag in request.if_none_match:
        response = current_app.response_class(status=304)
    else:
        response = current_app.response_class(body, mimetype="application/json")
    response.set_etag(etag)
    return response


@bp.get("/api/tiles")
def tiles():
    library = _services().library
    return jsonify(
        ok=True,
        rev=library.rev,
        tiles=[tile.to_public(cover_url(tile.cover)) for tile in library.tiles()],
    )


@bp.post("/api/tiles/<tile_id>/play")
def play(tile_id: str):
    runtime = _services().runtime
    try:
        result = runtime.play_tile(tile_id)
    except TileNotFound as exc:
        raise ApiError(404, "tile_not_found") from exc
    except Busy as exc:
        raise ApiError(409, "busy") from exc
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc
    document = runtime.state_document()
    status = 202 if result == "accepted" else 200
    return jsonify(ok=True, result=result, pending=document["pending"]), status


@bp.post("/api/transport/<action>")
def transport(action: str):
    try:
        playback = _services().runtime.transport(action)
    except ValueError as exc:
        raise ApiError(404, "not_found") from exc
    except Busy as exc:
        raise ApiError(409, "busy") from exc
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc
    return jsonify(ok=True, playback=playback)


@bp.post("/api/volume/<direction>")
def volume(direction: str):
    try:
        result = _services().runtime.change_volume(direction)
    except ValueError as exc:
        raise ApiError(404, "not_found") from exc
    except Busy as exc:
        raise ApiError(409, "busy") from exc
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc
    return jsonify(ok=True, volume=result)


@bp.get("/covers/<name>")
def cover(name: str):
    path = _services().covers.path(name)
    if path is None:
        abort(404)
    # Cover names are content hashes: a changed image gets a new URL.
    return send_file(path, mimetype="image/jpeg", max_age=365 * 24 * 3600)
