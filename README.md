<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Muckebox

A touch-friendly web UI that lets small children play music and audio
plays on a Sonos speaker by themselves: big cover tiles, one tap to play, a
volume limit and usage times that parents can rely on, and a few calm games
that need little screen.

> **Status:** pre-release. All features below are implemented and tested
> automatically against a simulated speaker and in real browser engines
> (iPad Safari and Android Chrome engines). Room search, playback of radio,
> Apple Music and NAS favorites and the volume limit are tested on real
> Sonos hardware; for the newer features see
> [docs/hardware-checklist.md](docs/hardware-checklist.md). Please report
> problems.

## Features

- **Kids view:** large cover tiles; tapping one starts it; the playing tile
  is highlighted. Play/pause, previous/next, louder/quieter with a volume bar
  (a full bar means the maximum volume). A picture instead of text when the
  speaker or the server cannot be reached; the UI never hangs.
- **Content from your Sonos favorites:** anything saved as a favorite in the
  Sonos app can become a tile: Apple Music, Spotify and other services,
  radio stations, audiobooks, podcasts and your own music library on a NAS.
  Share links from Apple Music, Spotify, TIDAL and Deezer can be added too.
- **Volume limit enforced by the server:** the kids cannot go above the
  limit you set, and Muckebox turns the speaker back down within about two
  seconds if somebody raises it in the Sonos app or on the device.
- **Usage times:** one time window per weekday. Before the end the music
  slowly gets quieter, then it pauses and a moon replaces the tiles until
  the next morning. Parents allow more time on the parents' page, or right
  on the kids tablet with a hidden 3-second press on the moon and the PIN.
- **Sleep timer:** the kids start it themselves with a moon button; after
  the chosen time the music fades out and the tiles rest until morning.
- **"Weiterhören":** albums (usually audio plays) continue at the episode
  and second where they stopped, even after other tiles played.
- **Games with little screen:** a freeze dance with the Sonos music, a sound
  quiz, "move like an elephant" and a breathing exercise for falling
  asleep. Each game is off until the parents enable it; three levels for
  kids of different ages; one daily time limit; short rounds with a calm
  end, no points, no endless mode, no microphone.
- **Parents' page** at `/admin`, protected by a PIN: choose the room, set the
  volume limit, usage times, sleep timer, games and your PIN, add favorites
  and share links as tiles, rename, reorder, remove, upload your own cover
  pictures. No restart needed.
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
- For the games' voice: a German text-to-speech voice on the tablet (see
  [Tablet setup](#tablet-setup-kiosk)). The games also work without one.

## Installation on a Synology NAS

These steps use DSM 7.2 or newer with the **Container Manager** package.

1. **Get Muckebox.** Either
   - download this repository as a ZIP file (*Code → Download ZIP*), upload
     it to the shared folder `docker` with File Station and extract it there.
     This creates a folder `Muckebox-main`; rename it to `muckebox`. Or
   - clone it over SSH (the target folder must not exist yet):
     `git clone https://github.com/onewaypub/Muckebox.git /volume1/docker/muckebox`
2. **Create the data folder.** In File Station, create a folder `data` inside
   `docker/muckebox`, next to `docker-compose.yml`. The project path is then
   usually `/volume1/docker/muckebox`.
3. **Find your user and group IDs.** Muckebox runs as the DSM user that owns
   the `data` folder. Over SSH, `id <your DSM user>` prints them, for example
   `uid=1026(...) gid=100(users)`. Make sure this user has read/write access
   to `docker/muckebox/data`.
4. **Edit `docker-compose.yml`** (e.g. with the Text Editor package): set
   `user:` to your IDs, e.g. `"1026:100"`. Everything else can stay as it is.
5. **Create the project.** Container Manager → *Project* → *Create*:
   name `muckebox`, path `/volume1/docker/muckebox`, source *Use existing
   docker-compose.yml*. Confirm; Container Manager builds the image and
   starts the container. The first build takes a few minutes.
6. **Find the first PIN.** Container Manager → *Container* → `muckebox` →
   *Log*. On its first start Muckebox creates a PIN for the parents' page and
   writes it to the log: `Parents' PIN: 482913 ...`. It is shown there on
   every start until you set your own PIN.
7. **Set up Muckebox.** On the parents' phone, go to
   `http://<NAS address>:8484/admin` and log in with that PIN. Muckebox
   searches the Sonos rooms by itself; choose the kids room (see
   [Network](#network) if no room is found). Then set the volume limit and
   your own PIN, and add tiles. The kids view is at
   `http://<NAS address>:8484/`.
8. **Check the clock.** The status on the parents' page shows the Muckebox's
   time and time zone. If it differs from your phone's, tap *Zeitzone …
   übernehmen*; the usage times follow that clock.

If the DSM firewall is enabled, allow TCP port 8484 from your tablets.
Muckebox uses plain HTTP and is meant for your home network only; do not
expose it to the internet.

**Changing settings:** room, volume limit and PIN are changed on the parents'
page and apply at once. Only `user:`, `PORT` and `LISTEN` live in
`docker-compose.yml`; to change them: Container Manager → *Project* →
`muckebox` → stop → *YAML configurations* (or edit the file) → *Build* →
start.
**Updating:** your settings, tiles and covers stay in `data`. With a ZIP:
extract the new version over `docker/muckebox` and set `user:` (and `PORT` or
`LISTEN`, if you changed them) in the new `docker-compose.yml` again. With
git: `git stash && git pull && git stash pop`. Then build and start the
project again.
**Updating from a version without settings on the parents' page** (where
`docker-compose.yml` still has `SONOS_ROOM` and `ADMIN_PIN`): the old and new
`docker-compose.yml` conflict. With git, run `git stash && git pull && git
stash drop` instead; with a ZIP, use the new file. Set `user:` again, start
Muckebox, log in with the PIN from the log and choose the room, the volume
limit and your PIN on the parents' page.
**Forgotten PIN:** Container Manager → *Container* → `muckebox` →
*Terminal* → *Create* → `bash`, then run
`python -m muckebox.admin reset-pin` (or on other hosts:
`docker exec muckebox python -m muckebox.admin reset-pin`). It prints a new
PIN and logs out all devices; Muckebox uses it right away, without a restart.
**Backup:** back up the `data` folder. It holds your tiles, covers and
settings, plus what Muckebox remembers (the last tile, where albums
stopped, today's game minutes, a running override or sleep timer). Your own
PIN is stored only as a hash; the PIN Muckebox generated is stored in plain
text until you set your own.
**Logs:** Container Manager → *Container* → `muckebox` → *Log*.

### Other Docker hosts

```sh
git clone https://github.com/onewaypub/Muckebox.git && cd Muckebox
mkdir data
# edit docker-compose.yml: user
docker compose up -d --build
docker compose logs muckebox   # shows the first PIN
```

Then open `http://<host>:8484/admin` and set up Muckebox as described above.

## Network

Muckebox needs these connections:

| From | To | Port | Why |
|---|---|---|---|
| Tablets, parents' phone | Muckebox host | TCP 8484 (`PORT`) | The web UI |
| Muckebox host | Sonos speakers | TCP 1400 | Controlling the speakers |
| Muckebox host | Sonos speakers | UDP 1900 multicast | Only without a speaker address on the parents' page: for the room search and to find the chosen room again after each start |
| Muckebox host | Internet | TCP 443, some cover art TCP 80, and DNS | When the parents' page shows favorites (thumbnails) and when parents add favorites or share links. Share-link lookups use HTTPS only. |

**Speakers in another VLAN** (for example an IoT network in UniFi): the
room search uses multicast, which does not cross VLANs. Enter the IP address
of a Sonos speaker under *Einrichtung* on the parents' page, best the kids
room's own (give it a fixed address with a DHCP reservation), and search
again; Muckebox reads the list of all rooms from that speaker. Allow the host
to reach the **whole speaker network** on TCP 1400, because the room you
control and its group coordinator may be different speakers. No rule from the
speakers back to the host is needed.

**Several Sonos systems in one network:** the search without a speaker
address shows the rooms of the system that answers first. Enter the address
of a speaker of the right system to see its rooms.

## Configuration

Room, speaker address, volume limit (default 25), step of the volume buttons
(default 3) and PIN are set on the parents' page and saved in
`data/settings.json`. Only what Muckebox needs before it can read that file
is set with environment variables:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8484` | HTTP port (1024–65535). Ports 1400–1499 (Sonos), the Synology DSM ports 5000, 5001 and 5357, and ports that browsers block are rejected. |
| `LISTEN` | `all` | Where Muckebox can be reached: `all` (the whole network, needed for tablets), `localhost` (this computer only, e.g. for trying it out) or one IPv4 address of the host. |
| `DATA_DIR` | `/data` | Directory for the settings, the tile library and cover images. |
| `TZ` | system | Time zone for the usage times, e.g. `Europe/Berlin`. The parents' page shows the clock and can take over the zone of the parent's browser instead. |

Earlier versions read `SONOS_ROOM`, `SONOS_IP`, `MAX_VOLUME`, `VOLUME_STEP`
and `ADMIN_PIN` from the environment. They are ignored now (the log says so)
and can be removed; set these values on the parents' page instead.

If `PORT` or `LISTEN` is invalid, the port is already in use, or `DATA_DIR`
is not writable, Muckebox cannot start: it writes the reason to the log and
exits. Docker then restarts it again and again, so Container Manager shows the
container as restarting and the log repeats the message until you fix the
setting (stop the project, correct it, start it again).

**Tip:** as a second safety net, also set a volume limit for the kids room in
the Sonos app (room settings → Volume Limit). Sonos scales the volume range
with that limit rather than cutting it off, so the effective maximum becomes
`Muckebox limit × Sonos limit / 100` (for example 25 × 50 / 100 ≈ 12). That
limit also applies when Muckebox is not running.

## Using Muckebox

### Parents' page (`/admin`)

- **Status:** connection, room, volume limit, corrections of the volume
  guard, the Muckebox's clock and anything that needs your attention.
- **Einrichtung:** shows the chosen room and finds the others; choosing one
  tests the connection first and applies the volume limit to it at once.
- **Kacheln** and **Sonos-Favoriten:** the favorites list shows every
  favorite of your household. *Hinzufügen* turns it into a tile, with its
  cover. Favorites that cannot be controlled over the network (TV input,
  "shortcuts" such as an artist) are shown greyed out with the reason. To use
  something that is not a favorite yet, save it as a favorite in the Sonos
  app first, or paste a share link under *Link hinzufügen*. Tiles can be
  renamed, moved and removed, and you can upload your own picture (JPEG or
  PNG, up to 10 MB). For album tiles, *Weiterhören* shows where the album
  stopped; *Von vorn* starts it from the beginning next time.
- **Lautstärke:** the limit (1–100) and the step of the louder/quieter
  buttons.
- **Nutzungszeiten:** switch them on and set one window per weekday (or
  "ganzer Tag frei"), and how many minutes before the end the music fades.
  The status line says what applies right now. *+15/+30/+60 Min.* and *Bis
  morgen früh* allow more time; *Freigabe beenden* ends that early.
- **Einschlaf-Timer:** show the moon button to the kids, its duration, and
  until when the tiles rest on days without a usage window.
- **Spiele:** enable each game and pick its level (small 2–3, middle 4–5,
  big 6+ years), the minutes per day for all games together, and the tile
  that plays the freeze-dance music. *Quellen der Klänge und Bilder* lists
  where the sounds and pictures come from.
- **PIN ändern:** all other devices are logged out afterwards.

### Kids view (`/`)

- Tapping a tile starts it; tapping the playing tile again does nothing,
  tapping it while paused continues. Albums continue where they stopped
  (*Weiterhören*); audiobooks and podcast episodes do so on their own.
  Shuffle and repeat are switched off when a tile starts, so that stories
  play in order. If the room is grouped with other rooms in the Sonos app,
  the whole group plays; the volume limit applies to the kids room.
- **When the usage time ends**, the volume bar shrinks and a small moon
  appears; then the music pauses and a big moon replaces the tiles. Pause
  and quieter still work. If the room plays in a group with other music, it
  is only faded, because a pause would stop the other rooms too. After the
  pause, the volume from before the fade is back for the next morning.
  Parents allow more time by holding the moon for 3 seconds and typing the
  PIN (digits only; with a PIN that contains letters, use the parents'
  page).
- **The moon button** (if enabled) starts the sleep timer; a ring shows the
  time left. At the end the music fades and pauses, and the tiles rest until
  the next window (or the wake time).
- **The games button** appears when a game is enabled and may run. The
  breathing exercise is also offered at bedtime, on the moon screen.
  Freeze dance: dance while the music plays, freeze when it stops (the
  screen only shows a calm colour). Sound quiz: listen and tap the right
  picture. "Beweg dich wie …": move like the animal the voice names. The
  cross in the corner ends a game at any time; unused game time is given
  back.

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

**Voice for the games:** the iPad reads the games' words with its own
German voice (*Settings → Accessibility → Spoken Content → Voices →
German*; a downloaded "Enhanced" voice sounds best). The iPad's mute switch
does not silence the games; its volume buttons do. Muckebox cannot keep the
screen on over plain HTTP: set *Display Auto-Lock* in Guided Access long
enough for the breathing exercise (5 minutes), or to *Never*.

### Android tablet with Fully Kiosk Browser

1. Install [Fully Kiosk Browser](https://www.fully-kiosk.com/) and set the
   *Start URL* to `http://<NAS address>:8484/`.
2. Recommended settings: *Keep Screen On*, *Reload on Network Reconnect*,
   *Autoreload on Page Error*, and disable pinch zoom.
3. Locking the tablet into the app (*Kiosk Mode*) is a feature of the paid
   *PLUS* license; Android's own *App pinning* (Settings → Security) is a free
   alternative.
4. **Voice for the games:** install a German voice for the system's
   text-to-speech (Settings → Accessibility → Text-to-speech, e.g. Google's
   speech services with the German voice data downloaded). Fully Kiosk
   Browser has no browser speech, but Muckebox uses Fully's own text to
   speech when its *JavaScript Interface* is enabled (Advanced Web
   Settings). Without a voice the games still work with pictures and
   sounds. Allow audio without a tap (*Autoplay Audio*) if sounds stay
   silent.

## Troubleshooting

The kids view shows a picture instead of the tiles when something is wrong:

| Picture | Meaning | What to check |
|---|---|---|
| Cloud with a cross | The tablet cannot reach Muckebox. | Is the container running (or restarting: see its log)? Tablet in the right network? DSM firewall? |
| Speaker | Muckebox cannot reach the Sonos speaker. | Speaker switched on? Room still there in the Sonos app? Firewall between the VLANs? UPnP enabled in the Sonos app? The parents' page shows the reason. |
| Tools | Muckebox is not set up yet. | Choose a room on the parents' page. |
| Moon | The usage time is over or the sleep timer has ended. | Wait for the next window, or hold the moon for 3 seconds and type the PIN, or allow more time on the parents' page. Wrong times? Check the clock in the parents' status. |

The parents' page shows the connection status and configuration problems in
plain words.

**Games:** no sound at all? Check the tablet's volume (the games play from
the tablet, only the freeze dance plays on the Sonos). No voice? Install a
German text-to-speech voice (see [Tablet setup](#tablet-setup-kiosk)); the
games work without one too. The games button is missing? The game is not
enabled, today's game time is used up, or bedtime is near: games end before
the music starts to fade.

For a closer look, run the diagnostics inside the container
(Container Manager → *Container* → `muckebox` → *Terminal*, or
`docker exec -it muckebox ...`):

```sh
python -m muckebox.diag rooms         # lists the rooms Muckebox can find
python -m muckebox.diag status        # finds the room, shows volume and playback
python -m muckebox.diag favorites     # lists favorites and how each one is played
python -m muckebox.diag play FV:2/3   # plays one favorite by its ID
python -m muckebox.diag watch-volume  # shows every volume correction for 60 s
```

They use the room chosen on the parents' page; `--room <name>` and
`--ip <speaker address>` (before the command) try another one without saving
it.

## Development

Contributions are welcome; please read the [Code of Conduct](CODE_OF_CONDUCT.md).
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
| [tzdata](https://github.com/python/tzdata) | Apache-2.0 (the time zone data itself is public domain) |

The Docker image is based on the official `python:3.13-slim-trixie` image
(Debian; Python under the PSF License).

Development tools are used only to build and test Muckebox and are not part
of the Docker image: pytest, pytest-cov and coverage (MIT, Apache-2.0),
requests-mock (Apache-2.0), Playwright (Apache-2.0), ruff (MIT), bandit
(Apache-2.0), pip-audit (Apache-2.0), pip-tools (BSD-3-Clause), gitleaks
(MIT), ESLint and eslint-plugin-compat (MIT), Stylelint and
stylelint-no-unsupported-browser-features (MIT).

### Sounds and pictures

The games' sounds are short excerpts of recordings from Wikimedia Commons
(public domain, CC0, and CC BY-SA for the dog); the pictures
are Twemoji graphics (CC BY 4.0). Authors and sources of every file are
listed in [docs/credits.md](docs/credits.md) and on the parents' page.

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
