# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTML pages, the web app manifest and icons."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, render_template

from muckebox.i18n import CATALOGUES, DEFAULT_LANG

from .icons import SIZES, app_icon

bp = Blueprint("pages", __name__)

THEME_COLOUR = "#1d3557"


def _page(template: str):
    response = current_app.make_response(
        render_template(template, lang=DEFAULT_LANG, messages=CATALOGUES[DEFAULT_LANG])
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.get("/")
def kids():
    return _page("kids.html")


@bp.get("/admin")
def admin():
    return _page("admin.html")


@bp.get("/manifest.webmanifest")
def manifest():
    response = jsonify(
        name="Muckebox",
        short_name="Muckebox",
        lang=DEFAULT_LANG,
        start_url="/",
        scope="/",
        display="fullscreen",
        orientation="any",
        background_color=THEME_COLOUR,
        theme_color=THEME_COLOUR,
        icons=[
            {
                "src": f"/icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any maskable",
            }
            for size in (192, 512)
        ],
    )
    response.mimetype = "application/manifest+json"
    return response


@bp.get("/apple-touch-icon.png")
def apple_touch_icon():
    return _icon(180)


@bp.get("/icon-<int:size>.png")
def icon(size: int):
    if size not in SIZES:
        abort(404)
    return _icon(size)


def _icon(size: int):
    response = current_app.response_class(app_icon(size), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
