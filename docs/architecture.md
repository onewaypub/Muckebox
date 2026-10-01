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
louder/quieter. Parents choose the room, set the volume limit and manage the
tiles on a PIN-protected admin page.

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

Non-goals for now (the design leaves seams for them, see
[Seams for later features](#seams-for-later-features)): night mode with a
lower limit, reading titles aloud, several rooms or profiles, a
battery-powered ESP32 touch client.

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
   - state poll every 2 s                              clamp to the limit
              │                                      - kids louder/quieter
              └──────────────► CircuitBreaker ◄──────────────┘
                                     │
                                     ▼
                     SoCo (local UPnP/SOAP, TCP 1400)
                     (a third "setup" lane searches rooms and tests a
                      newly chosen room, see "Choosing the room")
                                     │
                                     ▼
                              Sonos speaker(s)
```

### Components

| Component | Responsibility |
|---|---|
| `muckebox.config` | Parses the start-up variables `LISTEN`, `PORT` and `DATA_DIR` into an immutable `Settings` object. |
| `muckebox.settings` | The parents' settings in `settings.json`: room (name and speaker ID), speaker address, volume limit and step, PIN hash. Hands out immutable snapshots and notices changes by other processes. |
| `muckebox.admin` | Command line: `reset-pin` for a forgotten PIN. |
| `muckebox.web` | Flask app factory, kids and admin endpoints, JSON error envelope, security headers. `create_app()` starts no threads. |
| `muckebox.i18n` | One message catalogue (German in v1, English keys) used by Python and served to the browser. |
| `muckebox.sonos` | The only package that imports `soco`. Room search and resolution, favorites, playback routing, share-link playback, error classification, `FakeSonos` and `FakeHousehold` for tests and demo mode. |
| `muckebox.runtime` | Worker lanes, room sessions, circuit breaker, state cache, volume guard, command policy, the time keeper (usage times, override, sleep timer), resume positions and the games' server side. |
| `muckebox.schedule` | Pure time logic of the usage windows: open, fading, closed; overrides and the sleep lock as intervals. |
| `muckebox.localtime` | The time zone: parents' choice, `TZ`, the system, or UTC. |
| `muckebox.assets` | Sources and licences of the games' sounds and pictures. |
| `muckebox.library` | Tile model and the JSON store in `DATA_DIR`. |
| `muckebox.covers` | Downloads or accepts cover images, normalises them with Pillow, stores them content-addressed. |
| `muckebox.linkmeta` | Parses share links and fetches title/cover without API keys. |
| `muckebox.diag` | Command-line diagnostics that run inside the container. |
| `muckebox/static`, `muckebox/templates` | Kids and admin pages: static HTML, native ES modules, plain CSS. No build step. The games run in the browser (`games.js`, pure logic in `gamelogic.js`, sound and voice in `audio.js`); sounds and pictures are in `static/sounds` and `static/pictures`. |

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

## The two kids layouts

The parents choose the layout of the kids view: `small` (0–6 years) or `big`
(7–14 years). The page is rendered with the chosen one (no flash of the
other), and `/api/state` carries it in `view`, so a tablet switches without
a reload. The buttons exist once; `kids.js` moves them into the slots of the
current layout (the bar for `small`, the "what is playing" panel and the
header for `big`). `small` pages its tiles six at a time with CSS scroll
snapping; the tile size comes from container query units, so six tiles
always fit without scrolling in both orientations. Tiles without a cover show
an animal picture chosen from the tile's id, on a colour from the same id.

For `big`, the transport lane reads the position about every 10 s while a tile
with a queue plays (the same read that "Weiterhören" uses) and publishes the
track's number, title and seconds; the tablet counts the seconds on locally.

The pages use two typefaces shipped in `muckebox/static/fonts/` (Bricolage
Grotesque and Figtree, SIL OFL 1.1), so no page loads anything from outside.

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

The room is chosen on the parents' page; nothing about it is configured at
start. SSDP multicast discovery does not cross VLAN boundaries, so the room
search (`find_rooms()`) works in two ways:

- With a **speaker address** entered by the parents (the IP of any Sonos
  speaker in the household, best the kids room's own), Muckebox reads the
  zone group topology from it: one request with a fixed timeout lists every
  room. Other rooms are never contacted.
- Without one, it uses SSDP discovery and, if that finds nothing, a scan of
  the local network (at most 64 parallel probes). Only the first household
  that answers is shown; the speaker address selects another one.

The chosen room is saved with its **name and speaker ID** (UID). On later
lookups the room is found by its ID first and by its name only as a
fallback, so a room renamed in the Sonos app keeps working and its new name
is saved and logged.

- A stereo pair or home-theater satellite is mapped to its visible room.
- Once found, the kids room's own speaker is asked first on later lookups,
  and the speaker address only as a fallback. A failed lookup keeps the last
  known room, so the volume guard keeps working while that speaker is
  switched off.

### Choosing the room

Everything that belongs to the controlled room lives in a `RoomSession`:
its backend, its own volume guard, its circuit breakers, the connection
status and the favorites cache. Without a chosen room there is no session:
the lanes run but do nothing, the state says `not_configured`, and commands
are refused with that code.

Choosing a room is "test, then save": a new backend resolves the room on a
separate **setup lane** (which also runs room searches, one at a time), so a
slow test never blocks the kids. Only if the room answers are its name, ID and
the speaker address saved; then the session is swapped under a lock. Every
job holds on to the session it started with, and results of a replaced
session are dropped. So the old room's guard can never touch the new room,
and no breaker, error or highlighted tile carries over. `state.json` is tagged
with the room's speaker ID; choosing the same room again (e.g. with a new
speaker address) keeps the highlight.

### Groups

The tablet only ever controls the kids room. Before a tile starts and before
play, pause, next or previous, Muckebox looks the room up afresh; if it is
grouped, it leaves the group (`BecomeCoordinatorOfStandaloneGroup` on its own
player) and the other rooms play on. Transport and queue commands go to the
group **coordinator**, which is then the kids room itself (looked up again
when older than 5 seconds). The volume limit applies to the **kids room's own player**:
Sonos group volume is an average of its members, so clamping it would not cap
the kids room.

### Playing a favorite

Every favorite is classified when the parents' page lists it; the tile stores
that route with its snapshot and uses it when played:

| Favorite | Route |
|---|---|
| Radio stream (`x-sonosapi-stream`, `x-sonosapi-radio`, `x-rincon-mp3radio`, `aac:`, `hls-radio:` …), class `audioBroadcast*` | **direct**: `play_uri(uri, meta)` |
| Line-in (`x-rincon-stream`) | **direct** |
| Audiobook or podcast episode (class `audioBook` / podcast item) | **direct**, so that Sonos resumes the position |
| Album, playlist, Sonos playlist, NAS library folder or track, streaming-service container | **queue**: `clear_queue` → `add_to_queue` → `play_from_queue` |
| TV input (`x-sonos-htastream`), "shortcut" favorites without a resource | **not playable** (shown greyed out with a reason on the admin page) |

Resilience rules: UPnP error 714 or 402 switches to the other route once.
An 800 while filling the queue (e.g. a music service refreshing its token)
clears the queue and tries once more; a 701 from Play right after a new
source was set is retried once. Shuffle and repeat are set to normal once
the queue is the active source (best effort), so that audio plays keep their
order.

A favorite tile is stored as a **snapshot** (URI, DIDL metadata, class,
route), so it keeps playing without browsing the favorites first, even if the
favorite is renamed or removed in the Sonos app. The parents' page marks
favorites that already have a tile (matched by URI).

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

- The kids' `GET` endpoints (`/api/state`, `/api/tiles`, covers) read only
  from the in-memory `StateCache` and the library. The parents' favorites
  list runs a job on the transport lane (cached for 60 s); favorite artwork is
  downloaded in the request thread, at most two at a time.
- Mutating requests are handed to a worker lane. Starting a tile returns
  `202 Accepted` with a `pending` state immediately; the tablet shows progress
  and polls the state.
- Every UPnP call passes its own timeout: 3 s normally, 1.5 s for the volume
  guard, 30 s for filling the queue. SoCo's global timeout is set to 3 s as a
  safety net.
- A command that times out is cancelled, so it cannot run later; louder and
  quieter taps are refused (`409 busy`) while one is still running.
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
  above the limit, it sets it back to the limit. Worst-case exposure is about
  two seconds. A new limit applies at the next poll.
- Kids "louder/quieter" set an absolute, clamped value; they never overshoot.
  If the current volume cannot be read, nothing is changed.
- Repeated corrections within a short time are reported as a "fight" (for
  example somebody holding the volume button) and logged; enforcement
  continues.
- The README recommends also setting the native Sonos volume limit as defence
  in depth. Note that it scales rather than clamps: the effective maximum is
  `Muckebox limit × native limit / 100`.

UPnP event subscriptions are not used in v1: they need an extra firewall
rule from the speakers to the NAS and have known reliability issues. The guard
exposes `on_volume_observed()` so events can be added later.

## Data

Everything lives in `DATA_DIR` (a bind mount in Docker):

| File | Content |
|---|---|
| `library.json` | Tiles: schema version, revision counter, tile list. Written atomically (temp file, fsync, rename) with a `.bak` copy; a failed write leaves the library unchanged. A corrupt file is moved aside and reported, never silently discarded. |
| `covers/<hash>.jpg` | Normalised cover images, content-addressed. |
| `settings.json` | The parents' settings (schema version, room name and ID, speaker address, limit, step, scrypt PIN hash with its parameters; the generated PIN in plain text only until the parents set their own). Mode 0600, written atomically without a `.bak` copy (so no old PIN lingers), under a file lock shared with `reset-pin`. Every change re-reads the file under the lock first; the server re-reads it at most once a second when it changed. A corrupt file is moved aside and reported; a file from a newer Muckebox stops the start instead of being overwritten. |
| `settings.lock` | The lock file for `settings.json`. |
| `secret_key` | Random key for signing the admin session (mode 0600). |
| `state.json` | The last started tile and the speaker ID of its room, so the "now playing" highlight survives a restart. |
| `timers.json` | Override interval, sleep timer and lock, the last handled end, the volume before a fade, today's game seconds, the game-mute flag. Written from the transport lane only (never in a request, never under a lock). |
| `resume.json` | Per album tile: track, second, length, track URI and queue length. Written on pause, stop, tile switch, every 5 minutes while playing and at shutdown, so the NAS disk can sleep. |

No personal data about the children is stored. See
[PRIVACY.md](../PRIVACY.md) for the full privacy statement.

## Security model

- The kids view is reachable by anyone on the home network without login.
  Mutating requests require the header `X-Muckebox: 1`, which a cross-site
  form cannot send without a CORS preflight.
- The admin area needs the PIN (at least 4 characters; `change-me`, `1234`
  and `0000` are refused). On the first start Muckebox generates a random
  6-digit PIN and logs it on every start until the parents set their own.
  The PIN is stored as a salted scrypt hash (at most two hashes are computed
  at a time); failed logins are rate limited per client and globally, and a
  new PIN clears the counters. The session cookie is `HttpOnly`,
  `SameSite=Strict`, expires after 12 hours and is bound to the PIN hash, so
  every PIN change (on the parents' page or with `reset-pin`) ends all
  sessions. Admin mutations also check `Origin`. Shell access to the
  container is trusted: it can reset the PIN.
- Outbound internet requests (share-link lookups, metadata and covers,
  favorite artwork that is not served by a speaker) go through one fetcher:
  public addresses only, redirects checked on every hop, size limits and
  deadlines; share-link requests are further limited to the services' hosts.
  Artwork served by a speaker of the household is fetched from it directly,
  without following redirects.
- Uploads are limited in size and sniffed with Pillow; the client file name
  is ignored.
- A Content Security Policy forbids inline scripts. The UI loads no
  third-party resources.
- Muckebox uses plain HTTP inside the home network. It is not meant to be
  exposed to the internet. It does not check the requested host name, so a
  DNS-rebinding page could reach the kids view's functions (see SECURITY.md);
  the parents' page is protected by its host-bound login cookie.

## Testing strategy

- **Unit and API tests** (pytest) run without a speaker: `FakeSonos`
  implements the backend interface and `FakeHousehold` simulates several
  rooms, an injected clock makes timing
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

## Usage times, sleep timer and games

- **Time logic** (`muckebox.schedule`) is pure: one window per weekday,
  built per local calendar day with `zoneinfo` (so daylight saving days are
  right). A parents' override is an extra allowed interval; the kids' sleep
  lock is an interval in which nothing is allowed. `evaluate()` answers
  open, fading or closed, and gives the latest end at or before now.
- **Pausing once:** the transport lane pauses when that latest end has not
  been handled yet and is at most 15 minutes old, then records it. So a
  restart right after 19:00 still pauses, and later nothing ever pauses
  music that adults start from the Sonos app. A grouped room leaves its
  group instead of pausing it, so the other rooms play on.
- **Fading** is a soft limit in the volume guard: from the volume when the
  fade began down to 20 %. It is not counted as a correction. After the
  confirmed pause the earlier volume is restored.
- **No tap for a long time:** every tap on the tablet (tile, transport,
  volume, game) calls `TimeKeeper.touch()`; while no tile of ours plays, the
  transport lane touches it too, so only uninterrupted tile playback counts.
  After `idle_minutes` the same soft limit fades for one minute and the
  transport lane pauses once and restores the volume. This end lives in
  memory only (a restart counts as a tap) and never locks the tiles.
- **"Anti disco":** `Cooldown` closes a group of taps (tile, next/previous,
  play/pause) for a few seconds after an accepted tap; refused taps get
  `409 cooling_down`. Only kids' taps (`tap=True`) are checked, never the
  runtime's own starts (e.g. the freeze-dance music).
- **Commands** pass `TimeKeeper.check()` (the former `CommandPolicy` seam):
  while closed only pause and quieter are allowed; others get `409 bedtime`.
- **Resume** (`ResumeStore`): positions are read every 10 s while an album
  tile plays. Starting it again seeks to the track (checking that the same
  track is still there) and 5 s before the second; a refused seek is retried
  once after Play and never fails the start.
- **Games:** the tablet runs them; the server grants a round (daily limit,
  never into the fade), gives back unused time, and for the freeze dance
  mutes the kids room's own player. Every mute is a 12 s lease that the
  volume lane ends when it is not renewed, so the speaker never stays
  silent by accident; after a crash it is unmuted on the next start.

## Seams for later features

| Later feature | Seam |
|---|---|
| Night mode with a lower limit | The volume guard's soft limit (used by the fade) and `Runtime.volume_limit()`. |
| Several rooms or profiles | Everything per room already lives in a `RoomSession`; `settings.json` and `library.json` carry schema versions and tolerate unknown fields. |
| ESP32 battery client | `/api/state` carries `state_rev` and an `ETag`, so a client can poll cheaply with `If-None-Match`. Covers are baseline JPEGs. |
| Another Sonos backend | The Sonos backend is an interface; only `muckebox.sonos` knows SoCo. |
| Push events | `VolumeGuard.on_volume_observed()` and `StateCache.update()` accept observations from any source. |
| More games | A runner in `games.js`, its logic in `gamelogic.js`, an entry in `muckebox.settings.GAMES` and, if it counts, in `runtime.games`. |

## Status and roadmap

All features are implemented and covered by automated tests (unit and API
tests against `FakeSonos`, node tests for the browser logic, end-to-end tests
in WebKit and Chromium). The basics are tested on real Sonos hardware; the
open hardware checks are in [hardware-checklist.md](hardware-checklist.md).

Ideas for later (see [Seams for later features](#seams-for-later-features)):
night mode with a lower limit, reading titles aloud, several rooms or
profiles, an ESP32 touch display or NFC cards as a battery-powered client.
