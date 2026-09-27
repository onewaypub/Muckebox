<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Muckebox HTTP API

This is the contract between the Muckebox server and its clients: the kids
view, the admin page and future clients such as an ESP32 touch display.

**Status:** draft. Endpoints are implemented milestone by milestone; the
contract is frozen with v1.0. After that, fields are only added, and breaking
changes increase the `api` number reported by `/api/state`.

## Conventions

- Request and response bodies are JSON (`application/json; charset=utf-8`),
  except cover uploads (`multipart/form-data`) and cover images.
- Every `POST`, `PUT`, `PATCH` and `DELETE` request must send the header
  `X-Muckebox: 1`. Requests without it are rejected with `403 csrf_header_missing`.
- API responses carry `Cache-Control: no-store`.
- Errors use one envelope. `code` is a stable, English message key; clients
  translate it with the i18n catalogue. `retry_in` (seconds) is optional.

  ```json
  {"ok": false, "error": {"code": "sonos_unreachable", "retry_in": 8}}
  ```

- Successful responses contain `"ok": true`.

## Kids view and devices (no login)

| Method and path | Purpose | Responses |
|---|---|---|
| `GET /api/health` | Liveness of the web server (also used by the Docker health check). Independent of Sonos. | `200 {"ok": true, "version": "…"}` |
| `GET /api/state` | Current state from the server's cache; never waits for Sonos. Supports `ETag` / `If-None-Match`. | `200` (below), `304` |
| `GET /api/tiles` | Tiles in display order. | `200 {"ok": true, "rev": 7, "tiles": [{"id": "…", "title": "…", "cover": "/covers/….jpg" or null}]}` |
| `POST /api/tiles/<id>/play` | Start a tile. Tapping the tile that is already playing is a no-op; tapping it while paused resumes. | `202 {"ok": true, "pending": {…}}`, `200 {"ok": true, "noop": true}`, `404 tile_not_found`, `409 busy`, `503 sonos_unreachable` |
| `POST /api/transport/toggle`, `…/play`, `…/pause`, `…/next`, `…/previous` | Transport control on the group coordinator. | `200 {"ok": true, "playback": {…}}`, `409 busy`, `503 …` |
| `POST /api/volume/up`, `POST /api/volume/down` | Change the volume by `VOLUME_STEP`, clamped to `0…MAX_VOLUME`. | `200 {"ok": true, "volume": {…}}`, `503 volume_unknown` |
| `GET /covers/<hash>.jpg` | Cover image (600×600 JPEG, immutable). | `200`, `404` |

### State document

```json
{
  "ok": true,
  "api": 1,
  "version": "1.0.0",
  "state_rev": 42,
  "library_rev": 7,
  "sonos": {"status": "ok", "room": "Kids room", "retry_in": null},
  "playback": {
    "state": "playing",
    "tile_id": "t3f9c2a7e1b04d88",
    "can_toggle": true,
    "can_next": true,
    "can_prev": true
  },
  "volume": {"value": 12, "max": 25, "step": 3},
  "pending": null,
  "last_error": null
}
```

- `sonos.status`: `starting`, `ok`, `unreachable`, `upnp_disabled`,
  `room_not_found`, `group_problem`, `config_error`.
- `playback.state`: `playing`, `paused`, `stopped`, `transitioning`, `unknown`.
- `pending`: `null` or `{"action": "start", "tile_id": "…", "since": <unix time>}`.
- `last_error`: `null` or `{"code": "…", "tile_id": "…", "at": <unix time>}`;
  cleared after a successful start or after 60 seconds.
- `state_rev` increases whenever anything in the document changes.
  `library_rev` increases whenever the tile list changes.

## Admin (session cookie)

All admin endpoints return `403 admin_locked` when `ADMIN_PIN` is not set.
Mutations additionally require a valid session and a same-origin `Origin`
header.

| Method and path | Purpose |
|---|---|
| `GET /api/admin/session` | `{"ok": true, "locked": false, "logged_in": true}` |
| `POST /api/admin/login` `{"pin": "…"}` | Start a session. `401 pin_wrong`, `429 pin_rate_limited` (with `retry_in`). |
| `POST /api/admin/logout` | End the session. |
| `GET /api/admin/status` | Diagnostics for parents: room, coordinator, connection state, volume guard statistics, recent errors with hints, versions, source code link. |
| `GET /api/admin/favorites[?refresh=1]` | Sonos favorites with `playable`, `reason` and whether a tile already exists. |
| `GET /api/admin/tiles` | Tiles including source details. |
| `POST /api/admin/tiles` | Create a tile: `{"source": "favorite", "item_id": "FV:2/5"}` or `{"source": "sharelink", "url": "…"}`. `201`, or `422 not_playable` / `sharelink_unsupported` / `sharelink_unresolvable`. |
| `PATCH /api/admin/tiles/<id>` `{"title": "…", "rev": 8}` | Rename a tile. |
| `POST /api/admin/tiles/<id>/move` `{"direction": "up" \| "down", "rev": 8}` | Reorder. |
| `DELETE /api/admin/tiles/<id>?rev=8` | Remove a tile and its unused cover. |
| `PUT /api/admin/tiles/<id>/cover` (multipart field `cover`) | Upload a custom cover (JPEG or PNG, at most 10 MB). `413 upload_too_large`, `422 upload_not_image`. |

A stale `rev` on any mutation returns `409 rev_conflict`.
