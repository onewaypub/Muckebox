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

- Request and response bodies are JSON (`application/json`, UTF-8), except
  cover uploads (`multipart/form-data`) and cover images.
- Request bodies are limited to 1 MiB (cover uploads: 10 MiB). Larger bodies
  get `413 request_too_large` (uploads: `413 upload_too_large`); grossly
  oversized ones are rejected by the HTTP server itself with a plain-text
  `413`.
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
| `POST /api/tiles/<id>/play` | Start a tile. Tapping the tile that is already playing is a no-op; tapping it while paused resumes. | `202 {"ok": true, "result": "accepted", "pending": {…}}`, `200 {"ok": true, "result": "noop" \| "resumed", "pending": null}`, `404 tile_not_found`, `409 busy`, `503 <code>` |
| `POST /api/transport/play`, `…/pause`, `…/toggle`, `…/next`, `…/previous` | Transport control on the group coordinator. `play` and `pause` do nothing if the speaker is already in that state. | `200 {"ok": true, "playback": {…}}`, `409 busy`, `503 <code>` |
| `POST /api/volume/up`, `POST /api/volume/down` | Change the volume of the kids room by the step set on the parents' page, clamped to `0…limit`. One change at a time. | `200 {"ok": true, "volume": {…}}`, `409 busy`, `503 volume_unknown` (the volume could not be read or set) or `503 <code>` |
| `GET /covers/<hash>.jpg` | Cover image (600×600 JPEG, immutable). | `200`, `404` |
| `POST /api/sleep-timer/start` | The kids start the sleep timer (the moon button). Starting it again while it runs changes nothing. | `200 {"ok": true, "sleep_timer": {…}}`, `409 sleep_timer_off` / `bedtime` |
| `POST /api/override` `{"pin": "…", "minutes": 15\|30\|60}` or `{"pin": "…", "until": "morning"}` | Parents allow more time from the kids tablet: 15/30/60 minutes from now (or from the end of an override that is still running), or until the next window starts. Also ends the kids' sleep lock. Creates no session. Failed PINs are counted separately from the parents' page (5 per client, 20 in total, 15 minutes). | `200 {"ok": true, "schedule": {…}}`, `400`, `401 pin_wrong`, `409 schedule_off` (no usage times and no sleep lock), `429 pin_rate_limited` |

`409 bedtime` means the usage time is over (see `schedule` in the state).
`503 <code>` is the reason the speaker cannot be controlled right now:
`sonos_unreachable`, `upnp_disabled` or `room_not_found` (with `retry_in`),
`not_configured` (no room chosen yet), `sonos_timeout`, or another error
code such as `service_unavailable`.

### State document

```json
{
  "ok": true,
  "api": 1,
  "version": "1.0.0",
  "assets": "3f9c2a7e1b04",
  "state_rev": 42,
  "library_rev": 7,
  "sonos": {"status": "ok", "room": "Kids room", "grouped": false},
  "playback": {
    "state": "playing",
    "tile_id": "t3f9c2a7e1b04d88",
    "can_toggle": true,
    "can_next": true,
    "can_prev": true
  },
  "volume": {"value": 12, "max": 25, "limit": 25, "step": 3},
  "pending": null,
  "last_error": null,
  "schedule": {"phase": "open", "ends_at": 1790013600, "opens_at": null,
               "fade_from": 1790013000, "override_until": null},
  "sleep_timer": {"enabled": false, "minutes": 30, "ends_at": null}
}
```

- `sonos.status`: `not_configured` (no room chosen yet), `starting`, `ok`,
  or, when the speaker cannot be reached,
  `sonos_unreachable`, `upnp_disabled` or `room_not_found` (then
  `sonos.retry_in` gives the seconds until the next attempt). `grouped` is
  present once the room was found.
- `playback.state`: `playing`, `paused`, `stopped`, `transitioning`, `unknown`.
  `tile_id` is the tile Muckebox started, as long as it is still playing.
- `volume.value` is `null` while the volume is unknown. `max` is the parents'
  limit (the scale of the volume bar); `limit` is the cap right now, lower
  while the music fades before the end of the usage time or the sleep timer.
- `schedule.phase`: `off` (no usage times), `open`, `fading` (before
  `ends_at`, from `fade_from`) or `closed` (until `opens_at`). All times are
  Unix seconds. While closed, only pause and quieter are allowed; other
  commands get `409 bedtime`. `override_until` is set while the parents
  allow extra time.
- `sleep_timer.ends_at` is set while the kids' sleep timer runs. When it ends,
  the music fades and pauses, and `schedule.phase` stays `closed` until the
  next morning.
- `pending`: `null` or `{"action": "start", "tile_id": "…", "since": <unix time>}`.
- `last_error`: `null` or `{"code": "…", "tile_id": "…", "at": <unix time>}`;
  cleared after a successful start or after 60 seconds.
- `assets` identifies the build of the pages; the kids view reloads itself
  when it changes (after an update).
- `state_rev` increases when the server's cached speaker state changes;
  `library_rev` when the tile list changes. To detect any change, poll with
  `If-None-Match` and the `ETag` of the last answer.

## Admin (session cookie)

All admin endpoints except `session` and `login` return `401 login_required`
without a valid session. A session ends after 12 hours and whenever the PIN
changes (on the parents' page or with `reset-pin`). Mutations additionally
require a same-origin `Origin` header when the browser sends one
(`403 origin_mismatch`). Tile endpoints answer with the full tile list:
`{"ok": true, "rev": 8, "tiles": [...]}`.

| Method and path | Purpose |
|---|---|
| `GET /api/admin/session` | `{"ok": true, "logged_in": true}` |
| `POST /api/admin/login` `{"pin": "…"}` | Start a session. `401 pin_wrong`, `429 pin_rate_limited` (with `retry_in`). Failed attempts are counted per client (5) and in total (20) for 15 minutes, checks still running included; a new PIN clears the counters. |
| `POST /api/admin/logout` | End the session. |
| `POST /api/admin/pin` `{"current": "…", "new": "…"}` | Change the PIN. Ends all other sessions; this one stays logged in. `403 pin_wrong` (counted like a failed login), `429 pin_rate_limited`, `422 pin_too_short` / `pin_placeholder` / `pin_invalid`, `409 pin_changed` (the PIN was changed elsewhere, e.g. with `reset-pin`, while this request ran). Answers like `GET /api/admin/settings`. |
| `GET /api/admin/settings` | `{"ok": true, "settings": {"room": "Kids room" \| null, "seed_ip": "192.0.2.10" \| null, "max_volume": 25, "volume_step": 3, "pin_generated": false, "time_zone": null, "schedule": {…}, "sleep_timer": {…}, "games": {…}}, "sonos": {…}}`. Never contains PIN data. `pin_generated` is true while the PIN is still the one Muckebox created and printed in its log. |
| `PUT /api/admin/settings/schedule` `{"enabled": true, "fade_minutes": 10, "days": {"mon": {"from": "07:00", "to": "19:00"}, "tue": null, …}}` | Usage times: one window per weekday (`to` after `from` on the same day, `"24:00"` = midnight; `null` = no limit that day), fade 0–30 minutes. `422 schedule_invalid` / `schedule_order_invalid`. |
| `PUT /api/admin/settings/sleep-timer` `{"enabled": true, "minutes": 30, "wake": "07:00"}` | The kids' sleep timer: shown as a moon button when enabled; 5–90 minutes; afterwards the tiles stay locked until the next window, or until `wake` on days without one. `422 sleep_timer_invalid`. |
| `DELETE /api/admin/sleep-timer` | End a running sleep timer before it runs out. Answers with `schedule` and `sleep_timer`. |
| `POST /api/admin/override` `{"minutes": 15\|30\|60}` or `{"until": "morning"}`, `DELETE /api/admin/override` | Allow more time now, or end the override (the music fades and pauses again). `{"ok": true, "schedule": {…}}`; `409 schedule_off`. |
| `PUT /api/admin/settings/time-zone` `{"zone": "Europe/Berlin" \| null}` | The zone for the usage times; `null` falls back to `TZ` or the system zone. `422 time_zone_invalid`. Answers like `GET /api/admin/settings` (which includes `time_zone`). |
| `PUT /api/admin/settings/volume` `{"max_volume": 25, "volume_step": 3}` | Set the volume limit (1–100) and the step of the volume buttons (1–limit). Applies at once. `422 max_volume_invalid` / `volume_step_invalid`. |
| `POST /api/admin/rooms/search` `{"seed_ip": "…" \| null, "refresh": false}` | Find the household's rooms, through the speaker at `seed_ip` or by discovery. `{"ok": true, "rooms": [{"name": "…", "ip": "…", "grouped": false, "chosen": true}]}`. Results are reused for 15 s (with `refresh`, at most every 5 s). One search or room test at a time: `409 busy`. `422 seed_ip_invalid`, `503 <code>`. |
| `PUT /api/admin/settings/room` `{"room": "…", "seed_ip": "…" \| null}` | Choose the room to control. Muckebox connects to it first and saves it only on success; the volume limit applies to it at once. `422 room_invalid` / `seed_ip_invalid`, `409 busy`, `503 room_not_found` / `sonos_unreachable` / …, `500 settings_save_failed`. Answers like `GET /api/admin/settings`. |
| `GET /api/admin/status` | Diagnostics for parents: `version`, `source_url`, `tiles` (count), `sonos` (as in the state document), `time` (`now` as local ISO time, `zone`, `source`: `settings`, `TZ`, `system` or `default`), `schedule` (as in the state document), `config_problems` (codes: `not_configured`, `pin_generated`, `settings_corrupt`), `library_problem` (`null` or `{"code": "library_corrupt", "file": …}`), `volume_guard` (`max`, `corrections`, `fighting`, `fixed_volume`), `breaker` (`transport_retry_in`, `volume_retry_in`). |
| `GET /api/admin/favorites[?refresh=1]` | Sonos favorites: `item_id`, `title`, `description`, `playable`, `reason` (`no_resource`, `tv_input`, `broken_metadata`), `route` (`direct`, `queue`, `unsupported`), `has_art`, and `tile_id` if a tile already plays it. Cached for 60 s. |
| `GET /api/admin/favorite-art?item_id=FV:2/5` | The favorite's artwork as JPEG (fetched through Muckebox, because the speaker or image server may not be reachable from the parent's phone). |
| `GET /api/admin/tiles` | Tiles including source details. |
| `POST /api/admin/tiles` | Create a tile: `{"source": "favorite", "item_id": "FV:2/5"}` or `{"source": "sharelink", "url": "…"}`. `201` with `tile` and `warnings` (`cover_missing`, `title_missing`, `sharelink_experimental`); a favorite that already has a tile returns that tile. Errors: `404 favorite_not_found`, `422 not_playable` / `sharelink_unsupported` / `sharelink_unresolvable`. |
| `PATCH /api/admin/tiles/<id>` `{"title": "…", "rev": 8}` | Rename a tile. |
| `POST /api/admin/tiles/<id>/move` `{"direction": "up" \| "down", "rev": 8}` | Reorder. |
| `DELETE /api/admin/tiles/<id>?rev=8` | Remove a tile and its unused cover. |
| `PUT /api/admin/tiles/<id>/resume` `{"enabled": true\|false\|null, "rev": 8}` | "Weiterhören": the tile continues where it stopped. `null` = the default (on for albums, off for playlists; radio and other direct sources cannot resume). Tile JSON has `resume {available, enabled, default, position: {track, seconds} \| null}`. |
| `DELETE /api/admin/tiles/<id>/position` | "Von vorn": forget where the tile stopped. |
| `PUT /api/admin/tiles/<id>/cover` (multipart field `cover`) | Upload a custom cover (JPEG, PNG and other common formats, at most 10 MiB). `413 upload_too_large`, `422 upload_not_image`. |

A stale `rev` on any mutation returns `409 rev_conflict`.
