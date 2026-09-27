<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Muckebox architecture

This document describes how Muckebox v1 is built and why. It is the design
reference for contributors; user-facing documentation lives in the
[README](../README.md), the HTTP contract in [api.md](api.md).

## Goals and non-goals

Muckebox is a touch web UI that lets small children operate one Sonos
speaker on their own: big cover tiles, tap to play, play/pause, previous/next,
louder/quieter. Parents manage the tiles on a PIN-protected admin page.

Goals:

- **Safe for kids.** A maximum volume is enforced server-side, even when
  somebody turns the speaker up in the Sonos app or on the device.
- **Never hangs.** The kids view always responds quickly and shows a clear
  state when the server or the speaker is unreachable.
- **No cloud developer accounts.** Content comes from the Sonos favorites
  (Sonos already has the streaming services linked) and from share links.
  No Spotify Web API, no API keys.
- **Easy to self-host** as one Docker container, e.g. on a Synology NAS.
- **Private by default.** No telemetry, no third-party resources in the UI,
  all data stays in one local data directory.

Non-goals for v1 (the design leaves seams for them, see
[Seams for later features](#seams-for-later-features)): sleep timer or
bedtime lock, night mode with a lower limit, reading titles aloud, several
rooms or profiles, a battery-powered ESP32 touch client.

## Overview

```
Tablet (kids view) ─┐
Parent phone (admin) ├─HTTP─► waitress ─► Flask app
Later: ESP32 client ─┘                     │
                         GET  ─────────────┼──► StateCache (never calls Sonos)
                         POST ─► CommandPolicy (v1: allow all)
                                           │
              ┌────────────────────────────┴───────────────────────┐
              ▼                                                    ▼
   TransportLane (1 worker thread)                   VolumeLane (1 worker thread)
   - resolve room, find group coordinator            - Get/SetVolume on the kids
   - list favorites, start playback jobs               room's own player
   - transport commands                              - VolumeGuard: poll every 1 s,
   - state poll every 2 s                              clamp to MAX_VOLUME
              │                                      - kids louder/quieter
              └──────────────► CircuitBreaker ◄──────────────┘
                                     │
                                     ▼
                     SoCo (local UPnP/SOAP, TCP 1400)
                                     │
                                     ▼
                              Sonos speaker(s)
```

### Components

| Component | Responsibility |
|---|---|
| `muckebox.config` | Parses and validates environment variables into an immutable `Settings` object. |
| `muckebox.web` | Flask app factory, kids and admin endpoints, JSON error envelope, security headers. `create_app()` starts no threads. |
| `muckebox.i18n` | One message catalogue (German in v1, English keys) used by Python and served to the browser. |
| `muckebox.sonos` | The only package that imports `soco`. Room resolution, favorites, playback routing, share-link playback, error classification, a `FakeSonos` for tests. |
| `muckebox.runtime` | Worker lanes, circuit breaker, state cache, volume guard, command policy. |
| `muckebox.library` | Tile model and the JSON store in `DATA_DIR`. |
| `muckebox.covers` | Downloads or accepts cover images, normalises them with Pillow, stores them content-addressed. |
| `muckebox.linkmeta` | Parses share links and fetches title/cover without API keys. |
| `muckebox.diag` | Command-line diagnostics that run inside the container. |
| `muckebox/static`, `muckebox/templates` | Kids and admin pages: static HTML, native ES modules, plain CSS. No build step. |

Import rules (enforced by a test): `soco` is imported only inside
`muckebox.sonos`, and `muckebox.sonos` never imports Flask.

## Why these technologies

- **Python + [SoCo](https://github.com/SoCo/SoCo)** (MIT). SoCo talks to the
  speakers' local UPnP/SOAP API directly. It is actively maintained and used
  by Home Assistant. `node-sonos-http-api` is deliberately not used.
- **Flask + waitress.** SoCo is synchronous (it uses `requests`), so a
  synchronous web framework fits. waitress is pure Python and runs a single
  process, which guarantees that the background worker threads exist exactly
  once.
- **Pillow** normalises every cover to a 600×600 baseline JPEG, which every
  supported browser (and a future ESP32 client) can display.
- **No frontend build step.** Pages use native ES modules and modern CSS
  (grid, custom properties) within the supported browser baseline.

## Browser baseline

The kids view and the admin page support **iOS/iPadOS 16.4+ (Safari)** and
**Chrome / Android WebView 111+** (both released in spring 2023). CI checks
JavaScript and CSS against this baseline (ESLint with `eslint-plugin-compat`,
Stylelint with `no-unsupported-browser-features`, browserslist
`iOS >= 16.4, Chrome >= 111`).

Muckebox is served over plain HTTP inside the home network, so browser
features that need a secure context (service workers, installable PWAs on
Android) are not used.

## Sonos integration

### Finding the speaker

SSDP multicast discovery does not cross VLAN boundaries. Therefore:

- `SONOS_IP` is a **seed**: the IP of any Sonos speaker in the household.
  From it Muckebox reads the zone group topology, which lists every room.
- `SONOS_ROOM` is a **selector**: the room name, matched trimmed and
  case-insensitively against the topology.
- Both can be combined. With only `SONOS_ROOM`, Muckebox uses SSDP discovery
  (works only in the same network segment). With only `SONOS_IP`, the room
  that owns that IP is used.
- A stereo pair or home-theater satellite is mapped to its visible room.

### Groups

When the kids room is grouped with other rooms, transport and queue commands
go to the group **coordinator**, re-resolved before each command; the whole
group plays. The volume limit applies to the **kids room's own player**:
Sonos group volume is an average of its members, so clamping it would not cap
the kids room.

### Playing a favorite

Every favorite is classified when it is added and again when it is played:

| Favorite | Route |
|---|---|
| Radio stream (`x-sonosapi-stream`, `x-sonosapi-radio`, `x-rincon-mp3radio`, `aac:`, `hls-radio:` …), class `audioBroadcast*` | **direct**: `play_uri(uri, meta)` |
| Line-in (`x-rincon-stream`) | **direct** |
| Audiobook or podcast episode (class `audioBook` / podcast item) | **direct**, so that Sonos resumes the position |
| Album, playlist, Sonos playlist, NAS library folder or track, streaming-service container | **queue**: `clear_queue` → `add_to_queue` → `play_from_queue` |
| TV input (`x-sonos-htastream`), "shortcut" favorites without a resource | **not playable** (shown greyed out with a reason on the admin page) |

Resilience rules: UPnP error 714 or 402 switches to the other route once;
800 or 701 retries the failing step once. Shuffle and repeat are set to
normal when a tile starts (best effort), so that audio plays keep their order.

A favorite tile is stored as a **snapshot** (URI, DIDL metadata, class), so it
can be played without browsing the favorites first. The admin page matches
tiles against the current favorites (by URI, then by item ID) and marks tiles
whose favorite no longer exists.

### Share links

Share links (Apple Music, Spotify, TIDAL, Deezer) are parsed by Muckebox
itself into *(service, kind, id)* and handed to SoCo's `ShareLinkPlugin` as a
canonical URI, because the plugin's own URL patterns miss several real-world
formats. Sonos plays them with its default account for that service. Title
and cover are fetched without API keys (oEmbed, Open Graph or JSON-LD,
depending on the service), only when a parent adds the link.

Apple Music links are tested on real hardware; the other services are tested
with recorded fixtures only and are labelled experimental.

## Never hanging

A queue-based start can take 6–30 seconds for a large playlist, and some SoCo
calls have no timeout of their own. The design keeps the UI responsive anyway:

- HTTP `GET` handlers read only from the in-memory `StateCache`.
- Mutating requests are handed to a worker lane. Starting a tile returns
  `202 Accepted` with a `pending` state immediately; the tablet shows progress
  and polls the state.
- SoCo's global request timeout is 3 s; only the playback-start job raises it
  temporarily (scoped, restored on exceptions).
- A **circuit breaker** fails calls fast after connection errors, with a
  growing cooldown. It trips only on connection errors, never on a slow
  enqueue, and it never pauses the volume guard for more than about 5 s.
- The volume guard runs on its own lane, so a slow playback start can never
  delay a volume correction.

## Volume safety

The native Sonos volume limit can only be changed in the Sonos app; it cannot
be read or set through the local API. Muckebox therefore enforces its own
limit:

- The `VolumeGuard` polls the kids room player every second. If the volume is
  above `MAX_VOLUME`, it sets it back to `MAX_VOLUME`. Worst-case exposure is
  about two seconds.
- Kids "louder/quieter" set an absolute, clamped value; they never overshoot.
  If the current volume cannot be read, nothing is changed.
- Repeated corrections within a short time are reported as a "fight" (for
  example somebody holding the volume button) and logged; enforcement
  continues.
- The README recommends also setting the native Sonos volume limit as defence
  in depth. Note that it scales rather than clamps: the effective maximum is
  `MAX_VOLUME × native limit / 100`.

UPnP event subscriptions are not used in v1: they need an extra firewall
rule from the speakers to the NAS and have known reliability issues. The guard
exposes `on_volume_observed()` so events can be added later.

## Data

Everything lives in `DATA_DIR` (a bind mount in Docker):

| File | Content |
|---|---|
| `library.json` | Tiles: schema version, revision counter, tile list. Written atomically (temp file, fsync, rename) with a `.bak` copy. A corrupt file is moved aside and reported, never silently discarded. |
| `covers/<hash>.jpg` | Normalised cover images, content-addressed. |
| `secret_key` | Random key for signing the admin session (mode 0600). |
| `state.json` | The last started tile, so the "now playing" highlight survives a restart. |

No personal data is stored. See PRIVACY.md (added with the admin milestone)
for the full privacy statement.

## Security model

- The kids view is reachable by anyone on the home network without login.
  Mutating requests require the header `X-Muckebox: 1`, which a cross-site
  form cannot send without a CORS preflight.
- The admin area is locked unless `ADMIN_PIN` is set (at least 4
  characters). PIN comparison is constant-time; failed logins are rate
  limited per client and globally. The session cookie is `HttpOnly`,
  `SameSite=Strict`, expires after 12 hours and is bound to the current PIN,
  so changing the PIN ends all sessions. Admin mutations also check `Origin`.
- Outbound requests (cover downloads, share-link metadata) go through one
  fetcher with per-purpose host allowlists, redirect checks on every hop,
  size limits and deadlines.
- Uploads are limited in size and sniffed with Pillow; the client file name
  is ignored.
- A Content Security Policy forbids inline scripts. The UI loads no
  third-party resources.
- Muckebox uses plain HTTP inside the home network. It is not meant to be
  exposed to the internet.

## Testing strategy

- **Unit and API tests** (pytest) run without a speaker: `FakeSonos`
  implements the backend interface, an injected clock makes timing
  deterministic, and outbound HTTP is mocked. Real network access is blocked
  in tests. Backend coverage must stay at or above 90 %.
- **Frontend logic** in plain ES modules is tested with `node --test`.
- **End-to-end tests** with Playwright drive the kids view in WebKit (iPad
  viewport) and Chromium (Android tablet viewport) against the app running on
  `FakeSonos`.
- **Security and privacy checks** in CI: gitleaks (with extra rules for
  private IPs, email addresses, Sonos IDs), bandit, pip-audit, CodeQL, and a
  test that every source file carries an SPDX header.
- **Hardware checklists** per milestone cover what automation cannot:
  real speakers, real tablets, the NAS network path.

## Seams for later features

| Later feature | Seam in v1 |
|---|---|
| Sleep timer, bedtime lock | `CommandPolicy.check(command, context)` sits between the API and the lanes and allows everything in v1. |
| Night mode with a lower limit | The volume guard and the API read the limit from a `max_volume()` callable. |
| Several rooms or profiles | Runtime objects are keyed by a room id; `library.json` carries a schema version for migrations. |
| ESP32 battery client | `/api/state` carries `state_rev` and an `ETag`, so a client can poll cheaply with `If-None-Match`. Covers are baseline JPEGs. |
| Another Sonos backend | The Sonos backend is an interface; only `muckebox.sonos` knows SoCo. |
| Push events | `VolumeGuard.on_volume_observed()` and `StateCache.apply()` accept observations from any source. |
| Reading titles aloud | Would need the speakers to fetch audio from the NAS (an extra firewall rule); not prepared beyond stable tile ids and titles. |
