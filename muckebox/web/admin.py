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
from muckebox.schedule import schedule_to_json
from muckebox.settings import (
    SettingsError,
    SettingsFileError,
    games_to_json,
    sleep_timer_to_json,
    validate_seed_ip,
)
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
    return jsonify(ok=True, logged_in=auth.is_logged_in())


@bp.post("/login")
def login():
    pin = _body().get("pin")
    if not isinstance(pin, str):
        raise ApiError(400, "bad_request")
    auth.login(pin, _limiter)
    return jsonify(ok=True, logged_in=True)


@bp.post("/pin")
@auth.require_admin
def change_pin():
    """Set a new PIN. Ends all other sessions; this one stays logged in."""
    body = _body()
    try:
        checked = auth.check_pin(body.get("current"), _limiter)
    except ApiError as exc:
        if exc.code == "pin_wrong":
            # 403, not 401: the parent is still logged in.
            raise ApiError(403, "pin_wrong") from exc
        raise
    # Only if the PIN is still the one just checked (reset-pin may run meanwhile).
    updated = _settings_call(_services().store.change_pin, body.get("new"), checked)
    auth.start_session(updated.pin.version)
    log.info("The parents' PIN was changed")
    return _settings_response()


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


# -- settings -------------------------------------------------------------------------


def _settings_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except SettingsError as exc:
        raise ApiError(409 if exc.code == "pin_changed" else 422, exc.code) from exc
    except (OSError, SettingsFileError) as exc:
        log.error("Could not save the settings: %s", exc)
        raise ApiError(500, "settings_save_failed") from exc


def _settings_response():
    services = _services()
    current = services.store.current()
    return jsonify(
        ok=True,
        settings={
            "room": current.room,
            "seed_ip": current.seed_ip,
            "max_volume": current.max_volume,
            "volume_step": current.volume_step,
            "pin_generated": current.pin_generated,
            "time_zone": current.time_zone,
            "schedule": schedule_to_json(current.schedule),
            "sleep_timer": sleep_timer_to_json(current.sleep_timer),
            "games": games_to_json(current.games),
        },
        sonos=services.runtime.state.get("sonos"),
    )


@bp.get("/settings")
@auth.require_admin
def settings():
    return _settings_response()


@bp.put("/settings/volume")
@auth.require_admin
def set_volume():
    body = _body()
    _settings_call(_services().store.set_volume, body.get("max_volume"), body.get("volume_step"))
    return _settings_response()


@bp.put("/settings/schedule")
@auth.require_admin
def set_schedule():
    _settings_call(_services().store.set_schedule, _body())
    return _settings_response()


@bp.put("/settings/sleep-timer")
@auth.require_admin
def set_sleep_timer():
    _settings_call(_services().store.set_sleep_timer, _body())
    return _settings_response()


@bp.delete("/sleep-timer")
@auth.require_admin
def cancel_sleep_timer():
    """End a running sleep timer before it runs out."""
    keeper = _services().runtime.keeper
    keeper.cancel_sleep_timer()
    return jsonify(ok=True, **keeper.document())


@bp.post("/override")
@auth.require_admin
def override():
    """More time now: {"minutes": 15|30|60} or {"until": "morning"}."""
    body = _body()
    minutes, until = body.get("minutes"), body.get("until")
    if not (type(minutes) is int and minutes in (15, 30, 60)) and until != "morning":
        raise ApiError(400, "bad_request")
    keeper = _services().runtime.keeper
    keeper.override(minutes=None if until == "morning" else minutes, morning=until == "morning")
    return jsonify(ok=True, schedule=keeper.document()["schedule"])


@bp.delete("/override")
@auth.require_admin
def end_override():
    keeper = _services().runtime.keeper
    keeper.end_override()
    return jsonify(ok=True, schedule=keeper.document()["schedule"])


@bp.put("/settings/time-zone")
@auth.require_admin
def set_time_zone():
    """Use this zone for the usage times (e.g. the zone of the parent's browser)."""
    _settings_call(_services().store.set_time_zone, _body().get("zone"))
    return _settings_response()


@bp.put("/settings/room")
@auth.require_admin
def set_room():
    """Test the room and save it only if it answers."""
    body = _body()
    _settings_call(
        _runtime_call, _services().runtime.choose_room, body.get("room"), body.get("seed_ip")
    )
    return _settings_response()


@bp.post("/rooms/search")
@auth.require_admin
def search_rooms():
    body = _body()
    seed_ip = _settings_call(validate_seed_ip, body.get("seed_ip"))
    refresh = body.get("refresh") is True
    rooms = _runtime_call(_services().runtime.search_rooms, seed_ip, refresh)
    chosen = _services().store.current().room
    return jsonify(
        ok=True,
        rooms=[
            {"name": r.name, "ip": r.ip, "grouped": r.grouped, "chosen": r.name == chosen}
            for r in rooms
        ],
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


_art_downloads = threading.BoundedSemaphore(2)
ART_WAIT = 5.0  # seconds a thumbnail request waits for a download slot


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


def _find_favorite(item_id: str) -> Favorite:
    """Look the favorite up in the last list first; ask the speaker only if needed.

    Thumbnails and "add" clicks then do not queue behind a playback start.
    """
    runtime = _services().runtime
    for favorites in (runtime.cached_favorites() or [], None):
        if favorites is None:
            favorites = _runtime_call(runtime.favorites)
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
    jpeg = _favorite_art_jpeg(favorite.art_uri)
    if jpeg is None:
        raise ApiError(404, "not_found")
    response = current_app.response_class(jpeg, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


def _favorite_art_jpeg(art_uri: str) -> bytes | None:
    """Normalised artwork for a favorite, cached; None if it cannot be had.

    At most two downloads run at once, so that opening the parents' page
    never ties up the web server, and failures are remembered for a while.
    """
    cache = current_app.extensions.setdefault("muckebox.art_cache", _ArtCache())
    jpeg = cache.get(art_uri)
    if jpeg is not None:
        return jpeg or None
    if not _art_downloads.acquire(timeout=ART_WAIT):
        return None
    try:
        jpeg = normalise(_services().runtime.fetch_art(art_uri))
    except (SonosError, CoverError) as exc:
        log.info("No artwork for a favorite: %s", exc)
        jpeg = b""  # remembered as "not available"
    finally:
        _art_downloads.release()
    cache.put(art_uri, jpeg)
    return jpeg or None


# -- tiles ------------------------------------------------------------------------------


def _tile_json(tile) -> dict[str, Any]:
    source = tile.source
    summary = {"type": tile.kind}
    if tile.kind == "favorite":
        summary.update(description=source.get("description", ""), route=source.get("route"))
    else:
        summary.update(service=source["service"], kind=source["kind"], url=source.get("url"))
    saved = _services().runtime.resume.saved(tile.id)
    return {
        "id": tile.id,
        "title": tile.title,
        "cover": cover_url(tile.cover),
        "created_at": tile.created_at,
        "source": summary,
        "resume": {
            "available": tile.can_resume,
            "enabled": tile.resumes,
            "default": tile.resume_default,
            "position": {"track": saved.track, "seconds": saved.seconds} if saved else None,
        },
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
    existing = _tiles_by_uri().get(favorite.ref.uri)
    if existing is not None:
        # Already a tile (e.g. a repeated click after a slow answer).
        return services.library.get(existing), []
    warnings = []
    cover = None
    if favorite.art_uri:
        jpeg = _favorite_art_jpeg(favorite.art_uri)
        if jpeg:
            cover = services.covers.save(jpeg)
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


@bp.put("/tiles/<tile_id>/resume")
@auth.require_admin
def set_resume(tile_id: str):
    """Weiterhören for this tile: true, false or null (the default for its kind)."""
    body = _body()
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ApiError(400, "bad_request")
    _library_call(_services().library.set_resume, tile_id, enabled, _rev(body.get("rev")))
    return _tiles_response()


@bp.delete("/tiles/<tile_id>/position")
@auth.require_admin
def restart_tile(tile_id: str):
    """ "Von vorn": forget where the tile stopped."""
    services = _services()
    _library_call(services.library.get, tile_id)
    services.runtime.resume.clear(tile_id)
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
