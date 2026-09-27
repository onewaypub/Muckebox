<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Muckebox

A touch-friendly web UI that lets small children play music on a Sonos
speaker by themselves – big cover tiles, one tap to play, and a volume limit
that parents can rely on.

> **Status:** early development (milestone M0). Nothing is ready for use yet.
> See [docs/architecture.md](docs/architecture.md) for the design and the
> planned milestones.

## Features (planned for v1)

- **Kids view:** large cover tiles; tapping one starts it; the playing tile
  is highlighted. Play/pause, previous/next, louder/quieter with a volume bar.
  A clear message when the speaker cannot be reached – the UI never hangs.
- **Content from your Sonos favorites:** anything Sonos can play as a
  favorite (Apple Music, Spotify, radio, podcasts, audiobooks, your NAS music
  library) can become a tile. Share links from Apple Music, Spotify, TIDAL and
  Deezer can be added as well.
- **Volume limit enforced by the server**, even when somebody turns the
  speaker up in the Sonos app or on the device.
- **Parents' page** (`/admin`, protected by a PIN): add favorites and share
  links as tiles, rename, reorder, remove, upload your own cover.
- **Self-hosted and private:** one Docker container, no cloud accounts, no API
  keys, no telemetry.

## Requirements

- A Sonos system (S2) with **UPnP enabled** in the Sonos app
  (Account → Privacy & Security → Connection Security → UPnP).
- A host that runs Docker with host networking, for example a Synology NAS
  with Container Manager.
- A tablet with a current browser: **iOS/iPadOS 16.4 or newer** (Safari) or
  **Android with Chrome / WebView 111 or newer**.

## Installation

*Coming with milestone M1 (Docker image, docker-compose, Synology Container
Manager guide).*

## Tablet setup (kiosk)

*Coming with milestones M1 and M6 (iPad with Guided Access, Android with
Fully Kiosk Browser).*

## Network notes

Muckebox talks to the speakers over TCP port 1400. Automatic discovery by room
name uses multicast and only works when the host and the speakers are in the
same network (VLAN). If your speakers are in a different VLAN, set `SONOS_IP`
to the address of any speaker (ideally with a DHCP reservation) and allow
traffic from the host to the speakers' network on TCP 1400. Details follow
with milestone M2.

## Configuration

Muckebox is configured with environment variables.

| Variable | Default | Description |
|---|---|---|
| `SONOS_ROOM` | – | Name of the room to control (case-insensitive). |
| `SONOS_IP` | – | IP address of any Sonos speaker in your household. Required when the speakers are in another VLAN. At least one of `SONOS_ROOM` and `SONOS_IP` must be set. |
| `MAX_VOLUME` | `25` | Highest volume the kids can reach (Sonos volume value 0–100). Enforced by the server. |
| `VOLUME_STEP` | `3` | How much one tap on louder/quieter changes the volume (1–`MAX_VOLUME`). |
| `ADMIN_PIN` | – | PIN for the parents' page. Without a PIN (or with fewer than 4 characters) the parents' page stays locked. |
| `DATA_DIR` | `/data` | Directory for the tile library and cover images. |
| `PORT` | `8484` | HTTP port. Ports 1400–1499 (Sonos) and common NAS ports are rejected. |

If the Sonos or volume settings are invalid, Muckebox still starts and shows
the problem instead of playing music. An invalid `PORT` or an unusable
`DATA_DIR` stops the container with a clear log message.

## Development

Requires Python 3.13 or newer.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
scripts/check        # lint, tests, license headers, secret scan
python -m muckebox   # starts the server on http://localhost:8484
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
[docs/architecture.md](docs/architecture.md) for how Muckebox is built.

## Security and privacy

Muckebox is meant for your home network only. Please report security issues
as described in [SECURITY.md](SECURITY.md).

## Dependencies and licenses

Muckebox is licensed under AGPL-3.0-or-later. All runtime dependencies use
licenses that are compatible with it. The table lists every package installed
into the Docker image (kept in sync with `requirements.txt` by a test).

| Package | License |
|---|---|
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause |
| [Werkzeug](https://werkzeug.palletsprojects.com/) | BSD-3-Clause |
| [Jinja2](https://jinja.palletsprojects.com/) | BSD-3-Clause |
| [MarkupSafe](https://markupsafe.palletsprojects.com/) | BSD-3-Clause |
| [ItsDangerous](https://itsdangerous.palletsprojects.com/) | BSD-3-Clause |
| [Click](https://click.palletsprojects.com/) | BSD-3-Clause |
| [Blinker](https://blinker.readthedocs.io/) | MIT |
| [waitress](https://docs.pylonsproject.org/projects/waitress/) | ZPL-2.1 |

Development tools (pytest, ruff, bandit, pip-audit, pip-tools, gitleaks and
others) are used only to build and test Muckebox and are not part of the
Docker image.

## Support this project

Muckebox is a hobby project. If it makes your family's life a little easier,
you can support its development through the **Sponsor** button of this
repository (GitHub Sponsors or Ko-fi). Bug reports, translations and pull
requests are just as welcome.

## License

Copyright © 2026 Muckebox contributors.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE](LICENSE) for the full text.

Because Muckebox is a network service, the AGPL requires that users who
interact with a modified version over the network can get its source code.

Sonos is a trademark of Sonos, Inc. Muckebox is not affiliated with or
endorsed by Sonos, Apple, Spotify, TIDAL or Deezer.
