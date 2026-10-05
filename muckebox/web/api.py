# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON API for the kids view and other devices (no login)."""

from __future__ import annotations

import hashlib
import json

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from muckebox import __version__, assets
from muckebox.library import TileNotFound
from muckebox.runtime.cooldown import CoolingDown
from muckebox.runtime.games import UnknownGame
from muckebox.runtime.lights import LightsUnavailable
from muckebox.runtime.service import Busy, Unavailable

from . import auth
from .errors import ApiError

bp = Blueprint("api", __name__)
OVERRIDE_MINUTES = (15, 30, 60)


def _services():
    return current_app.extensions["muckebox"]


def cover_url(name: str | None) -> str | None:
    return f"/covers/{name}" if name else None


@bp.get("/api/health")
def health():
    """Liveness of the web server; independent of the Sonos connection."""
    return jsonify(ok=True, version=__version__)


@bp.get("/api/credits")
def credits():
    """Sources and licences of the games' sounds and pictures."""
    return jsonify(ok=True, credits=assets.credits())


@bp.get("/api/state")
def state():
    document = _services().runtime.state_document()
    document["assets"] = current_app.jinja_env.globals["asset_version"]
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
        result = runtime.play_tile(tile_id, tap=True)
    except TileNotFound as exc:
        raise ApiError(404, "tile_not_found") from exc
    except CoolingDown as exc:
        raise ApiError(409, exc.code, exc.retry_in) from exc
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
        playback = _services().runtime.transport(action, tap=True)
    except ValueError as exc:
        raise ApiError(404, "not_found") from exc
    except CoolingDown as exc:
        raise ApiError(409, exc.code, exc.retry_in) from exc
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


@bp.post("/api/lights/<int:slot>/toggle")
def toggle_light(slot: int):
    """A light button: its scene on, or the room off if the scene is on."""
    runtime = _services().runtime
    runtime.keeper.touch()  # a tap on the tablet
    try:
        lights = runtime.lights.toggle(slot)
    except LookupError as exc:
        raise ApiError(404, "not_found") from exc
    except CoolingDown as exc:
        raise ApiError(409, exc.code, exc.retry_in) from exc
    except LightsUnavailable as exc:
        raise lights_error(exc) from exc
    return jsonify(ok=True, lights=lights)


@bp.post("/api/diag/touch")
def touch_diagnosis():
    """The tablet's touch events while the parents run the touch diagnosis."""
    body = request.get_json(silent=True)
    events = body.get("events") if isinstance(body, dict) else None
    try:
        kept = _services().runtime.touchlog.add(events)
    except PermissionError as exc:
        raise ApiError(409, "diag_off") from exc
    except ValueError as exc:
        raise ApiError(400, "bad_request") from exc
    return jsonify(ok=True, kept=kept)


def lights_error(exc: LightsUnavailable) -> ApiError:
    """409 for what the parents can fix, 503 while the bridge is away."""
    if exc.code in ("hue_link_button", "hue_not_configured", "hue_not_found"):
        return ApiError(409, exc.code)
    return ApiError(503, exc.code, exc.retry_in)


@bp.get("/covers/<name>")
def cover(name: str):
    path = _services().covers.path(name)
    if path is None:
        abort(404)
    # Cover names are content hashes: a changed image gets a new URL.
    return send_file(path, mimetype="image/jpeg", max_age=365 * 24 * 3600)


@bp.post("/api/override")
def override():
    """Parents allow more time from the kids tablet (PIN, no session)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request")
    minutes, until = body.get("minutes"), body.get("until")
    if not (minutes in OVERRIDE_MINUTES and type(minutes) is int) and until != "morning":
        raise ApiError(400, "bad_request")
    auth.check_pin(body.get("pin"), auth.override_limiter)
    runtime = _services().runtime
    runtime.keeper.override(
        minutes=None if until == "morning" else minutes, morning=until == "morning"
    )
    return jsonify(ok=True, schedule=runtime.keeper.document()["schedule"])


@bp.post("/api/sleep-timer/start")
def start_sleep_timer():
    """The kids start the sleep timer with the moon button."""
    runtime = _services().runtime
    runtime.keeper.start_sleep_timer()  # Refused -> 409 sleep_timer_off / bedtime
    return jsonify(ok=True, sleep_timer=runtime.keeper.document()["sleep_timer"])


@bp.post("/api/games/<game_id>/start")
def start_game(game_id: str):
    runtime = _services().runtime
    try:
        game = runtime.start_game(game_id)
    except UnknownGame as exc:
        raise ApiError(404, "not_found") from exc
    except Busy as exc:
        raise ApiError(409, "busy") from exc
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc
    return jsonify(
        ok=True,
        game={
            "id": game.id,
            "level": game.level,
            "seconds": int(game.granted),
            "ends_at": int(game.ends_at),
        },
    )


@bp.post("/api/games/end")
def end_game():
    runtime = _services().runtime
    runtime.end_game()
    return jsonify(ok=True, games=runtime.games.document())


@bp.post("/api/games/freeze_dance/mute")
def freeze_dance_mute():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("muted"), bool):
        raise ApiError(400, "bad_request")
    try:
        _services().runtime.game_mute(body["muted"])
    except Unavailable as exc:
        raise ApiError(503, exc.code, exc.retry_in) from exc
    return jsonify(ok=True), 202
