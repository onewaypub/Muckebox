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
| `POST /api/tiles/<id>/play` | Start a tile, only in the kids room (a grouped kids room leaves its group first). Tapping the tile that is already playing is a no-op; tapping it while paused resumes. | `202 {"ok": true, "result": "accepted", "pending": {…}}`, `200 {"ok": true, "result": "noop" \| "resumed", "pending": null}`, `404 tile_not_found`, `409 busy`, `409 cooling_down` (with `retry_in`; see `cooldown`), `503 <code>` |
| `POST /api/transport/play`, `…/pause`, `…/toggle`, `…/next`, `…/previous` | Transport control of the kids room: if it is grouped, it leaves the group first (the other rooms play on). `play` and `pause` do nothing if the speaker is already in that state. | `200 {"ok": true, "playback": {…}}`, `409 busy`, `409 cooling_down` (with `retry_in`), `503 <code>` |
| `POST /api/volume/up`, `POST /api/volume/down` | Change the volume of the kids room by the step set on the parents' page, clamped to `0…limit`. One change at a time. | `200 {"ok": true, "volume": {…}}`, `409 busy`, `503 volume_unknown` (the volume could not be read or set) or `503 <code>` |
| `GET /covers/<hash>.jpg` | Cover image (600×600 JPEG, immutable). | `200`, `404` |
| `GET /api/credits` | Sources and licences of the games' sounds and pictures. | `200 {"ok": true, "credits": [{"name", "author", "license", "license_url", "source"}]}` |
| `POST /api/games/<id>/start` (`freeze_dance`, `sound_quiz`, `move_like`, `breathing`) | Start a game. The server grants a round: at most what is left of the day's game time and never into the bedtime fade. The freeze dance starts its dance music; the quiz and "move like" pause the speaker (their sounds come from the tablet). The breathing exercise is not counted. | `200 {"ok": true, "game": {"id", "level", "seconds", "ends_at"}}`, `404`, `409 game_unavailable` / `game_running` / `games_limit_reached` / `bedtime` / `dance_music_missing` / `busy`, `503 <code>` |
| `POST /api/games/end` | End the running game; unused time is given back. The freeze dance unmutes and pauses its music. | `200 {"ok": true, "games": {…}}` |
| `POST /api/games/freeze_dance/mute` `{"muted": true}` | Freeze (mute the kids room's own speaker) or dance on. A mute lasts 12 s unless renewed; the server also unmutes when the game ends, at bedtime or on a room change. | `202`, `400`, `409 game_unavailable` |
| `POST /api/lights/<slot>/toggle` | A light button (slot 1–3): its scene on, or the room off if the scene is on. Allowed at any time, also at bedtime. After a tap all light buttons wait 3 s. | `200 {"ok": true, "lights": {…}}`, `404`, `409 cooling_down` (with `retry_in`) / `hue_not_configured` / `hue_not_found`, `503 hue_unreachable` / `hue_certificate_changed` / `hue_unauthorized` |
| `POST /api/sleep-timer/start` | The kids start the sleep timer (the moon button). Starting it again while it runs changes nothing. | `200 {"ok": true, "sleep_timer": {…}}`, `409 sleep_timer_off` / `bedtime` |
| `POST /api/override` `{"pin": "…", "minutes": 15\|30\|60}` or `{"pin": "…", "until": "morning"}` | Parents allow more time from the kids tablet: 15/30/60 minutes from now (or from the end of an override that is still running), or until the next window starts. Also ends the kids' sleep lock. Creates no session. Failed PINs are counted per client separately from the parents' page (5 per client), but share one total of 20 per 15 minutes with it. | `200 {"ok": true, "schedule": {…}}`, `400`, `401 pin_wrong`, `409 schedule_off` (no usage times and no sleep lock), `429 pin_rate_limited` |

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
  "cooldown": {"tile": 5, "skip": 3, "toggle": 1},
  "view": {"profile": "small", "skip_buttons": false},
  "track": {"number": 4, "count": 9, "title": "Kapitel 4", "seconds": 750,
            "duration": 1970, "at": 1790013000, "playing": true},
  "progress": {"t3f9c2a7e1b04d88": 0.38},
  "lights": {"available": true, "cooldown": 3,
             "slots": [{"slot": 1, "picture": "sun", "active": false}]},
  "last_error": null,
  "schedule": {"phase": "open", "ends_at": 1790013600, "opens_at": null,
               "fade_from": 1790013000, "override_until": null},
  "sleep_timer": {"enabled": false, "minutes": 30, "ends_at": null},
  "games": {"remaining": 900, "active": null,
            "items": [{"id": "sound_quiz", "level": 2, "available": true}]}
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
- `games.items` lists the games the parents enabled, with their level and
  whether they can start now; `remaining` is today's game time in seconds.
- `sleep_timer.ends_at` is set while the kids' sleep timer runs. When it ends,
  the music fades and pauses, and `schedule.phase` stays `closed` until the
  next morning.
- `cooldown`: seconds that taps of the same kind wait after an accepted tap
  ("anti disco"): `tile` after a tile start (a setting, `0` = off), `skip`
  after next/previous, `toggle` after play/pause or resuming the loaded tile.
  The server enforces them; the tablet only dims the buttons meanwhile.
- `view`: the layout the parents chose: `profile` `small` (0–6 years) or
  `big` (7–14 years), and `skip_buttons` (previous/next on `small`).
- `track`: only on the `big` layout, while a tile with a queue plays: the
  track's number in the queue, the queue's length, its title (if the speaker
  knows it) and the position in seconds as read at `at` (Unix time, read
  about every 10 s). The tablet counts on from there while `playing`. `null`
  for radio and when nothing of ours plays.
- `progress`: how far each album tile with *Weiterhören* got (0–1), for the
  bar on its cover.
- `lights`: the light buttons the parents set up (empty `slots` without a
  Hue bridge, room or buttons): the slot number, its picture and whether its
  scene is on. Scenes deleted in the Hue app are left out. `available` is
  false while the bridge cannot be reached (the buttons are dimmed).
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
| `GET /api/admin/settings` | `{"ok": true, "settings": {"room": "Kids room" \| null, "seed_ip": "192.0.2.10" \| null, "max_volume": 25, "volume_step": 3, "pin_generated": false, "time_zone": null, "schedule": {…}, "sleep_timer": {…}, "games": {…}, "controls": {"tap_cooldown": 5, "idle_minutes": 60, "profile": "small", "skip_buttons": false}}, "sonos": {…}}`. Never contains PIN data. `pin_generated` is true while the PIN is still the one Muckebox created and printed in its log. |
| `PUT /api/admin/settings/schedule` `{"enabled": true, "fade_minutes": 10, "days": {"mon": {"from": "07:00", "to": "19:00"}, "tue": null, …}}` | Usage times: one window per weekday (`to` after `from` on the same day, `"24:00"` = midnight; `null` = no limit that day), fade 0–30 minutes. `422 schedule_invalid` / `schedule_order_invalid`. |
| `PUT /api/admin/settings/games` `{"daily_minutes": 15, "dance_tile": "t…" \| null, "items": {"sound_quiz": {"enabled": true, "level": 2}, …}}` | Games: each off by default, level 1 (2–3 years), 2 (4–5) or 3 (6+), one daily limit for all counted games (5–60 minutes), the tile that plays the freeze-dance music (null: whatever plays). `422 games_invalid`. The status adds `games {used_today, daily_seconds}`. |
| `PUT /api/admin/settings/controls` `{"tap_cooldown": 5, "idle_minutes": 60, "profile": "small", "skip_buttons": false}` | Seconds the other tiles wait after a tile tap (0–30, 0 = off), minutes a tile may play without any tap on the tablet before it fades for a minute and pauses (0–240, 0 = off), the layout of the kids view (`small` or `big`) and whether `small` shows previous/next. Fields left out get their defaults. `422 controls_invalid`. The status adds `idle {minutes, paused_at}` (local time of the last such pause, or null). |
| `PUT /api/admin/settings/sleep-timer` `{"enabled": true, "minutes": 30, "wake": "07:00", "lights": "keep"}` | The kids' sleep timer: shown as a moon button when enabled; 5–90 minutes; afterwards the tiles stay locked until the next window, or until `wake` on days without one. `lights`: at the end leave the lights (`keep`), switch the room `off`, or recall a scene (its id). `422 sleep_timer_invalid`. |
| `GET /api/admin/hue` | The paired bridge (`ip`, `id`, `name`; never the key or the certificate), the chosen room and buttons, `available`, `problem`, and read fresh from the bridge: `rooms` (with lights) and `scenes` (`id`, `name`, `room`). |
| `POST /api/admin/hue/search` `{"ip": "192.0.2.50" \| null}` | The bridge at that address, or all bridges found by mDNS (`_hue._tcp`; works across VLANs with an mDNS reflector). `{"ok": true, "bridges": [{"ip", "id", "name"}]}`. `422 hue_ip_invalid`, `503 hue_unreachable`. The Hue cloud is never asked. |
| `POST /api/admin/hue/pair` `{"ip": "…"}` | Pair with the bridge: works within 30 s after its button was pressed (`409 hue_link_button` until then; the page retries). The certificate seen now is pinned. Answers like `GET /api/admin/settings`. |
| `POST /api/admin/hue/reconnect` | Take over a new certificate of the paired bridge (e.g. after a firmware update) if the stored key still works (`503 hue_unauthorized` otherwise: pair again). |
| `DELETE /api/admin/hue` | Forget the bridge, the room and the buttons. |
| `PUT /api/admin/settings/hue` `{"room": "<id>", "slots": [{"scene": "<id>", "picture": "sun"}]}` | The room or zone and up to three light buttons; pictures `sun`, `book`, `star`, `moon`, `bulb`. `422 hue_invalid`. |
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
