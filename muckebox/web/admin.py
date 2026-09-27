# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON API for the parents' page (PIN-protected)."""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from muckebox import __version__
from muckebox.covers import CoverError, normalise
from muckebox.library import LibraryError, RevConflict, TileNotFound, favorite_source
from muckebox.runtime.service import Busy, Unavailable
from muckebox.sonos.errors import SonosError
from muckebox.sonos.model import Favorite

from . import auth
from .api import cover_url
from .errors import ApiError

log = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

SOURCE_URL = "https://github.com/onewaypub/Muckebox"
_UPLOAD_PATH_RE = re.compile(r"^/api/admin/tiles/[^/]+/cover$")
_limiter = auth.RateLimiter()


def is_upload_path(path: str) -> bool:
    return bool(_UPLOAD_PATH_RE.match(path))


def _services():
    return current_app.extensions["muckebox"]


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request")
    return data


def _rev(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "bad_request") from exc


def _library_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except TileNotFound as exc:
        raise ApiError(404, exc.code) from exc
    except RevConflict as exc:
        raise ApiError(409, exc.code) from exc
    except LibraryError as exc:
        raise ApiError(422, exc.code) from exc


def _runtime_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Busy as exc:
        raise ApiError(409, "busy") from exc
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc


# -- session ------------------------------------------------------------------------


@bp.get("/session")
def session_state():
    return jsonify(ok=True, locked=auth.is_locked(), logged_in=auth.is_logged_in())


@bp.post("/login")
def login():
    pin = _body().get("pin")
    if not isinstance(pin, str):
        raise ApiError(400, "bad_request")
    auth.login(pin, _limiter)
    return jsonify(ok=True, logged_in=True)


@bp.post("/logout")
def logout():
    auth.logout()
    return jsonify(ok=True, logged_in=False)


# -- status ---------------------------------------------------------------------------


@bp.get("/status")
@auth.require_admin
def status():
    services = _services()
    return jsonify(
        ok=True,
        version=__version__,
        source_url=SOURCE_URL,
        tiles=len(services.library.tiles()),
        **services.runtime.status(),
    )


# -- favorites ------------------------------------------------------------------------


class _ArtCache:
    """Small in-memory cache of normalised favorite artwork."""

    def __init__(self, size: int = 200) -> None:
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            return None

    def put(self, key: str, value: bytes) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


_art_cache = _ArtCache()


def _favorite_json(favorite: Favorite, tiles_by_uri: dict[str, str]) -> dict[str, Any]:
    return {
        "item_id": favorite.item_id,
        "title": favorite.title,
        "description": favorite.description,
        "playable": favorite.playable,
        "reason": favorite.reason,
        "route": favorite.route.value,
        "has_art": bool(favorite.art_uri),
        "tile_id": tiles_by_uri.get(favorite.ref.uri) if favorite.ref else None,
    }


def _tiles_by_uri() -> dict[str, str]:
    return {
        tile.source["uri"]: tile.id
        for tile in _services().library.tiles()
        if tile.kind == "favorite"
    }


def _find_favorite(item_id: str, refresh: bool = False) -> Favorite:
    favorites = _runtime_call(_services().runtime.favorites, refresh)
    for favorite in favorites:
        if favorite.item_id == item_id:
            return favorite
    raise ApiError(404, "favorite_not_found")


@bp.get("/favorites")
@auth.require_admin
def favorites():
    refresh = request.args.get("refresh") == "1"
    items = _runtime_call(_services().runtime.favorites, refresh)
    by_uri = _tiles_by_uri()
    return jsonify(ok=True, favorites=[_favorite_json(f, by_uri) for f in items])


@bp.get("/favorite-art")
@auth.require_admin
def favorite_art():
    favorite = _find_favorite(request.args.get("item_id", ""))
    if not favorite.art_uri:
        raise ApiError(404, "not_found")
    jpeg = _art_cache.get(favorite.art_uri)
    if jpeg is None:
        try:
            jpeg = normalise(_services().runtime.fetch_art(favorite.art_uri))
        except (SonosError, CoverError) as exc:
            raise ApiError(404, "not_found") from exc
        _art_cache.put(favorite.art_uri, jpeg)
    response = current_app.response_class(jpeg, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# -- tiles ------------------------------------------------------------------------------


def _tile_json(tile) -> dict[str, Any]:
    source = tile.source
    summary = {"type": tile.kind}
    if tile.kind == "favorite":
        summary.update(description=source.get("description", ""), route=source.get("route"))
    else:
        summary.update(service=source["service"], kind=source["kind"], url=source.get("url"))
    return {
        "id": tile.id,
        "title": tile.title,
        "cover": cover_url(tile.cover),
        "created_at": tile.created_at,
        "source": summary,
    }


def _tiles_response(status: int = 200, **extra: Any):
    library = _services().library
    body = {"ok": True, "rev": library.rev, "tiles": [_tile_json(t) for t in library.tiles()]}
    body.update(extra)
    return jsonify(body), status


@bp.get("/tiles")
@auth.require_admin
def tiles():
    return _tiles_response()


@bp.post("/tiles")
@auth.require_admin
def create_tile():
    body = _body()
    source = body.get("source")
    if source == "favorite":
        tile, warnings = _create_from_favorite(str(body.get("item_id", "")))
    elif source == "sharelink":
        tile, warnings = _create_from_share_link(str(body.get("url", "")))
    else:
        raise ApiError(400, "bad_request")
    return _tiles_response(201, tile=_tile_json(tile), warnings=warnings)


def _create_from_favorite(item_id: str):
    services = _services()
    favorite = _find_favorite(item_id)
    if not favorite.playable or favorite.ref is None:
        raise ApiError(422, "not_playable")
    warnings = []
    cover = None
    if favorite.art_uri:
        try:
            cover = services.covers.save(services.runtime.fetch_art(favorite.art_uri))
        except (SonosError, CoverError) as exc:
            log.info("No cover for favorite %s: %s", item_id, exc)
    if cover is None:
        warnings.append("cover_missing")
    source = favorite_source(favorite.item_id, favorite.ref, favorite.route, favorite.description)
    tile = _library_call(services.library.add, favorite.title[:60], source, cover)
    return tile, warnings


def _create_from_share_link(url: str):
    from . import sharelinks  # imported lazily: needs network helpers

    return sharelinks.create_tile(_services(), url)


@bp.patch("/tiles/<tile_id>")
@auth.require_admin
def rename_tile(tile_id: str):
    body = _body()
    title = body.get("title")
    if not isinstance(title, str):
        raise ApiError(400, "bad_request")
    _library_call(_services().library.rename, tile_id, title, _rev(body.get("rev")))
    return _tiles_response()


@bp.post("/tiles/<tile_id>/move")
@auth.require_admin
def move_tile(tile_id: str):
    body = _body()
    direction = body.get("direction")
    if direction not in ("up", "down"):
        raise ApiError(400, "bad_request")
    _library_call(_services().library.move, tile_id, direction, _rev(body.get("rev")))
    return _tiles_response()


@bp.delete("/tiles/<tile_id>")
@auth.require_admin
def delete_tile(tile_id: str):
    services = _services()
    _library_call(services.library.remove, tile_id, _rev(request.args.get("rev")))
    services.covers.delete_unused(services.library.covers_in_use())
    return _tiles_response()


@bp.put("/tiles/<tile_id>/cover")
@auth.require_admin
def upload_cover(tile_id: str):
    from .app import MAX_UPLOAD_BYTES

    services = _services()
    _library_call(services.library.get, tile_id)
    upload = request.files.get("cover")
    if upload is None:
        raise ApiError(400, "bad_request")
    data = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "upload_too_large")
    try:
        name = services.covers.save(data)
    except CoverError as exc:
        raise ApiError(422, "upload_not_image") from exc
    _library_call(services.library.set_cover, tile_id, name, _rev(request.form.get("rev")))
    services.covers.delete_unused(services.library.covers_in_use())
    return _tiles_response()
