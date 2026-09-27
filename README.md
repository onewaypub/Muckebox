<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Muckebox

A touch-friendly web UI that lets small children play music on a Sonos
speaker by themselves: big cover tiles, one tap to play, and a volume limit
that parents can rely on.

> **Status:** pre-release. All features below are implemented and tested
> automatically against a simulated speaker and in real browser engines.
> Tests on real Sonos hardware are in progress; please report problems.

## Features

- **Kids view:** large cover tiles; tapping one starts it; the playing tile
  is highlighted. Play/pause, previous/next, louder/quieter with a volume bar
  (a full bar means the maximum volume). A picture instead of text when the
  speaker or the server cannot be reached; the UI never hangs.
- **Content from your Sonos favorites:** anything saved as a favorite in the
  Sonos app can become a tile: Apple Music, Spotify and other services,
  radio stations, audiobooks, podcasts and your own music library on a NAS.
  Share links from Apple Music, Spotify, TIDAL and Deezer can be added too.
- **Volume limit enforced by the server:** the kids cannot go above
  `MAX_VOLUME`, and Muckebox turns the speaker back down within about two
  seconds if somebody raises it in the Sonos app or on the device.
- **Parents' page** at `/admin`, protected by a PIN: add favorites and share
  links as tiles, rename, reorder, remove, upload your own cover pictures.
- **Self-hosted and private:** one Docker container, no cloud accounts, no API
  keys, no telemetry. See [PRIVACY.md](PRIVACY.md).

## Requirements

- A Sonos system (S2) with **UPnP enabled** in the Sonos app: *Account →
  Privacy & Security → Connection Security → UPnP*. It is on by default.
- The music services you want to use, linked in the Sonos app.
- A host that runs Docker with host networking, for example a Synology NAS
  with Container Manager, and that can reach the speakers on TCP port 1400.
- A tablet with a current browser: **iOS/iPadOS 16.4 or newer** (iPad
  5th generation and later) or **Android with Chrome / WebView 111 or newer**.
  The parents' page works on any current phone or computer browser.

## Installation on a Synology NAS

These steps use DSM 7.2 or newer with the **Container Manager** package.

1. **Create the folders.** In File Station, create `docker/muckebox` on your
   volume and inside it a folder `data`. The path is then usually
   `/volume1/docker/muckebox`.
2. **Copy Muckebox into it.** Download this repository as a ZIP file
   (*Code → Download ZIP*) and extract it into `docker/muckebox`, or clone it
   there over SSH:
   `git clone https://github.com/onewaypub/Muckebox.git /volume1/docker/muckebox`
3. **Find your user and group IDs.** Muckebox runs as the DSM user that owns
   the `data` folder. Over SSH, `id <your DSM user>` prints them, for example
   `uid=1026(...) gid=100(users)`. Make sure this user has read/write access
   to `docker/muckebox/data`.
4. **Edit `docker-compose.yml`** (e.g. with the Text Editor package):
   - `user:` your IDs, e.g. `"1026:100"`;
   - `SONOS_ROOM` and/or `SONOS_IP` (see [Network](#network));
   - `MAX_VOLUME`, `VOLUME_STEP`;
   - `ADMIN_PIN`: your own PIN. Example values such as `change-me` or
     `1234` are refused and keep the parents' page locked.
5. **Create the project.** Container Manager → *Project* → *Create*:
   name `muckebox`, path `/volume1/docker/muckebox`, source *Use existing
   docker-compose.yml*. Confirm; Container Manager builds the image and
   starts the container. The first build takes a few minutes.
6. **Open Muckebox.** On the parents' phone, go to
   `http://<NAS address>:8484/admin`, log in with your PIN and add tiles.
   The kids view is at `http://<NAS address>:8484/`.

If the DSM firewall is enabled, allow TCP port 8484 from your tablets.
Muckebox uses plain HTTP and is meant for your home network only; do not
expose it to the internet.

**Changing settings:** Container Manager → *Project* → `muckebox` → stop →
*YAML configurations* (or edit the file) → *Build* → start.
**Updating:** replace the files (or `git pull`), then build and start the
project again. Your tiles and covers stay in `data`.
**Logs:** Container Manager → *Container* → `muckebox` → *Log*.

### Other Docker hosts

```sh
git clone https://github.com/onewaypub/Muckebox.git && cd Muckebox
mkdir data
# edit docker-compose.yml (user, SONOS_ROOM / SONOS_IP, ADMIN_PIN, ...)
docker compose up -d --build
```

## Network

Muckebox needs these connections:

| From | To | Port | Why |
|---|---|---|---|
| Tablets, parents' phone | Muckebox host | TCP 8484 (`PORT`) | The web UI |
| Muckebox host | Sonos speakers | TCP 1400 | Controlling the speakers |
| Muckebox host | Sonos speakers | UDP 1900 multicast | Only for finding a room by name without `SONOS_IP` |
| Muckebox host | Internet (HTTPS) | TCP 443 | Only when parents add share links or favorites with online cover art |

**Speakers in another VLAN** (for example an IoT network in UniFi):
discovery by room name uses multicast, which does not cross VLANs. Set
`SONOS_IP` to the address of any Sonos speaker (give it a fixed address with
a DHCP reservation) and keep `SONOS_ROOM` to pick the room; Muckebox reads the
list of all rooms from that speaker. Allow the host to reach the **whole
speaker network** on TCP 1400, because the room you control and its group
coordinator may be different speakers. No rule from the speakers back to the
host is needed.

## Configuration

Muckebox is configured with environment variables.

| Variable | Default | Description |
|---|---|---|
| `SONOS_ROOM` | – | Name of the room to control, as in the Sonos app (case-insensitive). |
| `SONOS_IP` | – | IP address of any Sonos speaker in your household. Required when the speakers are in another VLAN. At least one of `SONOS_ROOM` and `SONOS_IP` must be set. |
| `MAX_VOLUME` | `25` | Highest volume the kids can reach (1–100 on the Sonos volume scale). Enforced by the server. |
| `VOLUME_STEP` | `3` | How much one tap on louder/quieter changes the volume (1–`MAX_VOLUME`). |
| `ADMIN_PIN` | – | PIN for the parents' page, at least 4 characters. Without a valid PIN the parents' page stays locked. |
| `DATA_DIR` | `/data` | Directory for the tile library and cover images. |
| `PORT` | `8484` | HTTP port (1024–65535). Ports 1400–1499 (Sonos), the Synology DSM ports 5000, 5001 and 5357, and ports that browsers block are rejected. |

If the Sonos or volume settings are invalid, Muckebox still starts and shows
the problem instead of playing music. An invalid `PORT` or an unusable
`DATA_DIR` stops the container with a clear log message.

**Tip:** as a second safety net, also set a volume limit for the kids room in
the Sonos app (room settings → Volume Limit). Sonos scales the volume range
with that limit rather than cutting it off, so the effective maximum becomes
`MAX_VOLUME × Sonos limit / 100` (for example 25 × 50 / 100 ≈ 12). That limit
also applies when Muckebox is not running.

## Using Muckebox

**Parents' page** (`/admin`): the *Sonos favorites* list shows every favorite
of your household. *Add* turns it into a tile, with its cover. Favorites that
cannot be controlled over the network (TV input, "shortcuts" such as an
artist) are shown greyed out with the reason. To use something that is not a
favorite yet, save it as a favorite in the Sonos app first, or paste a share
link under *Add link*. Tiles can be renamed, moved and removed, and you can
upload your own picture (JPEG or PNG, up to 10 MB).

**Kids view** (`/`): tapping a tile starts it from the beginning; tapping the
playing tile again does nothing, tapping it while paused continues.
Audiobooks and podcast episodes continue where they stopped. Shuffle and
repeat are switched off when a tile starts, so that stories play in order.
If the room is grouped with other rooms in the Sonos app, the whole group
plays; the volume limit applies to the kids room.

## Tablet setup (kiosk)

### iPad

1. Open `http://<NAS address>:8484/` in Safari, tap *Share* → *Add to Home
   Screen*. Start Muckebox from the new icon; it opens without Safari's
   address bar.
2. Turn on **Guided Access**: *Settings → Accessibility → Guided Access*,
   set a passcode, and set *Display Auto-Lock* as you prefer.
3. Open Muckebox from the home screen and triple-click the top (or home)
   button to start Guided Access. The child cannot leave the app.
4. After the iPad restarts, open Muckebox from the home screen and start
   Guided Access again.

If Muckebox shows the "no connection" picture after a power cut, it recovers
by itself as soon as the NAS is back. If the page itself cannot load (for
example the NAS was off when the app was opened), end Guided Access
(triple-click), close the app and open it again.

### Android tablet with Fully Kiosk Browser

1. Install [Fully Kiosk Browser](https://www.fully-kiosk.com/) and set the
   *Start URL* to `http://<NAS address>:8484/`.
2. Recommended settings: *Keep Screen On*, *Reload on Network Reconnect*,
   *Autoreload on Page Error*, and disable pinch zoom.
3. Locking the tablet into the app (*Kiosk Mode*) is a feature of the paid
   *PLUS* license; Android's own *App pinning* (Settings → Security) is a free
   alternative.

## Troubleshooting

The kids view shows a picture instead of the tiles when something is wrong:

| Picture | Meaning | What to check |
|---|---|---|
| Cloud with a cross | The tablet cannot reach Muckebox. | Is the container running? Tablet in the right network? DSM firewall? |
| Speaker | Muckebox cannot reach the Sonos speaker. | Speaker switched on? `SONOS_IP` / `SONOS_ROOM` right? Firewall between the VLANs? UPnP enabled in the Sonos app? |
| Tools | Muckebox is not set up correctly. | The log and the parents' page show which setting is wrong. |

The parents' page shows the connection status and configuration problems in
plain words. For a closer look, run the diagnostics inside the container
(Container Manager → *Container* → `muckebox` → *Terminal*, or
`docker exec -it muckebox ...`):

```sh
python -m muckebox.diag status        # finds the room, shows volume and playback
python -m muckebox.diag favorites     # lists favorites and how each one is played
python -m muckebox.diag play FV:2/3   # plays one favorite by its ID
python -m muckebox.diag watch-volume  # shows every volume correction for 60 s
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup (including a
demo mode with a simulated speaker) and the checks, and
[docs/architecture.md](docs/architecture.md) for how Muckebox is built. The
HTTP API is described in [docs/api.md](docs/api.md).

## Security and privacy

Muckebox is meant for your home network only. Please report security issues
as described in [SECURITY.md](SECURITY.md). [PRIVACY.md](PRIVACY.md) explains
which data Muckebox stores and which connections it makes.

## Dependencies and licenses

Muckebox is licensed under AGPL-3.0-or-later. All runtime dependencies use
licenses that are compatible with it. The table lists every package installed
into the Docker image (kept in sync with `requirements.txt` by a test).

| Package | License |
|---|---|
| [SoCo](https://github.com/SoCo/SoCo) | MIT |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause |
| [Werkzeug](https://werkzeug.palletsprojects.com/) | BSD-3-Clause |
| [Jinja2](https://jinja.palletsprojects.com/) | BSD-3-Clause |
| [MarkupSafe](https://markupsafe.palletsprojects.com/) | BSD-3-Clause |
| [ItsDangerous](https://itsdangerous.palletsprojects.com/) | BSD-3-Clause |
| [Click](https://click.palletsprojects.com/) | BSD-3-Clause |
| [Blinker](https://blinker.readthedocs.io/) | MIT |
| [waitress](https://docs.pylonsproject.org/projects/waitress/) | ZPL-2.1 |
| [Pillow](https://python-pillow.github.io/) | MIT-CMU |
| [Requests](https://requests.readthedocs.io/) | Apache-2.0 |
| [urllib3](https://urllib3.readthedocs.io/) | MIT |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | MIT |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 |
| [lxml](https://lxml.de/) | BSD-3-Clause (its binary wheels bundle libxml2 and libxslt, MIT; libiconv, LGPL-2.1; zlib, Zlib) |
| [xmltodict](https://github.com/martinblech/xmltodict) | MIT |
| [ifaddr](https://github.com/ifaddr/ifaddr) | MIT |
| [appdirs](https://github.com/ActiveState/appdirs) | MIT |

The Docker image is based on the official `python:3.13-slim-trixie` image
(Debian; Python under the PSF License).

Development tools are used only to build and test Muckebox and are not part
of the Docker image: pytest, pytest-cov and coverage (MIT, Apache-2.0),
requests-mock (Apache-2.0), Playwright (Apache-2.0), ruff (MIT), bandit
(Apache-2.0), pip-audit (Apache-2.0), pip-tools (BSD-3-Clause), gitleaks
(MIT), ESLint and eslint-plugin-compat (MIT), Stylelint and
stylelint-no-unsupported-browser-features (MIT).

## Support this project

Muckebox is a hobby project. If it makes your family's life a little easier,
you will be able to support its development through GitHub Sponsors or Ko-fi
once these accounts are set up; a **Sponsor** button will then appear at the
top of this repository. Bug reports, translations and pull requests are just
as welcome.

## License

Copyright © 2026 Muckebox contributors.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE](LICENSE) for the full text.

Because Muckebox is a network service, the AGPL requires that users who
interact with a modified version over the network can get its source code.
The parents' page links to the source code.

Sonos is a trademark of Sonos, Inc. Muckebox is not affiliated with or
endorsed by Sonos, Apple, Spotify, TIDAL or Deezer.
