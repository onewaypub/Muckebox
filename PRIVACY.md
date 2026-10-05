<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Privacy

Muckebox runs entirely on your own device. The authors of Muckebox receive no
data from your installation: there are no accounts, no telemetry, no update
checks and no analytics.

This document describes what Muckebox stores and which connections it makes,
so that you can assess it, for example under the EU General Data Protection
Regulation (GDPR, German: DSGVO).

## Who is responsible

Whoever runs Muckebox (usually a family on its own NAS) is responsible for the
data it processes. Using Muckebox purely within your own household is
generally a "purely personal or household activity" (Art. 2(2)(c) GDPR). If
you run it elsewhere, for example in a kindergarten, the GDPR applies to you
as the operator.

## What Muckebox stores

Everything lives in the data folder (`DATA_DIR`):

| File | Content | Personal data? |
|---|---|---|
| `library.json` | The tiles: titles, the Sonos favorite or share link each one plays, file names of their covers. | Only what parents type in as titles. |
| `library.json.bak` | The previous version of `library.json`, kept as a safety copy. It is replaced on every change, so a renamed or removed title remains there until the next change. | Only what parents type in as titles. |
| `library.json.corrupt-…` | Only if `library.json` could not be read: the unreadable file, moved aside for inspection. Delete it once you no longer need it. | Only what parents type in as titles. |
| `covers/` | Cover pictures: album art and pictures uploaded by parents. Uploaded photos are re-encoded; their metadata (EXIF, e.g. location) is removed. | Only if parents upload photos of people. |
| `settings.json` | The settings from the parents' page: name and speaker ID of the chosen room, the speaker address if one was entered, volume limit and step, and the PIN as a salted scrypt hash. With Philips Hue also the bridge's address, id and name, the key it gave Muckebox and the fingerprint of its certificate, the chosen room and the scenes of the light buttons. While the PIN is still the one Muckebox generated, that PIN is also stored in plain text, so that it can be shown in the log; setting your own PIN removes it. Readable only by the Muckebox user (mode 0600). | The room name, if it contains a name (e.g. "Emma's room"); the same for Hue room and scene names. |
| `settings.lock` | An empty file that keeps Muckebox and `reset-pin` from writing the settings at the same time. | No |
| `settings.json.corrupt-…` | Only if `settings.json` could not be read: the unreadable file, moved aside. Delete it once you no longer need it. | As `settings.json`. |
| `secret_key` | A random key that signs the parents' login cookie. | No |
| `state.json` | Which tile Muckebox started last, and the speaker ID of its room. | No |
| `timers.json` | A running override of the usage time and its end, the kids' sleep timer, which end of a usage time was already handled, the volume before the last fade, **today's** game minutes (only the current day, no history), whether a game muted the speaker, and which end of the sleep timer already switched the lights. | Minimal: when the tablet was used today, as a single number. |
| `resume.json` | For album tiles: the track and second where each stopped ("Weiterhören"). *Von vorn* on the parents' page deletes it for a tile; removing a tile deletes it too. | Minimal: what was last listened to per album. |

Muckebox stores **nothing about the children** beyond the two small files
above: no usage history, no listening statistics, no names, no scores. The
games have no points and keep no results.

To delete everything, stop the container and delete the data folder.

## Cookies and logs

- **Cookies:** the kids view uses none. The parents' page sets one cookie
  after a successful PIN login (`muckebox_admin`). It is technically necessary
  for the login, expires after 12 hours and is deleted on logout.
- **Rate limiting:** failed PIN attempts are counted per client IP address in
  memory only. Entries older than 15 minutes are discarded at the next login
  attempt, and all of them when the PIN changes or Muckebox restarts. They
  are never written to disk.
- **Logs:** the container log contains technical messages (for example the
  Sonos room name, speaker addresses, errors) and no request logs with client
  addresses. It contains a PIN only while the PIN is the one Muckebox
  generated (on the first start or after `reset-pin`), so that parents can
  log in; a PIN you set yourself is never logged. Earlier log lines stay in
  the container log until Docker rotates it or the container is removed.

## Connections

| Connection | When | What is sent |
|---|---|---|
| Tablet/phone → Muckebox | While the pages are open | Taps and page requests, within your home network. The pages load nothing from third parties: no web fonts from the internet, no CDNs, no trackers. The fonts, the games' sounds and the pictures are part of Muckebox and come from it too. |
| The tablet's own text-to-speech | While a game speaks | The games' short sentences ("Richtig, die Kuh!") are spoken by the tablet's system voice. On iPads this runs on the device. Some Android voices may use an online service of the voice's provider, depending on the tablet's settings. No microphone and no camera are used. |
| Muckebox → Sonos speakers | Continuously while running | Control commands and status queries within your home network. |
| Muckebox → Hue Bridge | Only if you connected one: every few seconds while running | Which scene is active, and switching scenes or the room's lights, over HTTPS within your home network. The Hue cloud and the Hue account are not used; searching uses mDNS in the home network, never Philips' online discovery. |
| Sonos speakers → music services | When music plays | Sonos itself streams from the services you linked in the Sonos app, under their terms. Muckebox only tells the speaker what to play. |
| Muckebox → album art servers | When parents open or add Sonos favorites | Album art addresses provided by Sonos; some point to the music services' image servers on the internet. Only public internet addresses are contacted for these (besides the speakers themselves). |
| Muckebox → Apple Music, Spotify, TIDAL, Deezer | Only when parents add a share link | The link itself, to look up its title and cover (oEmbed or the public web page). These services see the public IP address of your internet connection. |

Muckebox makes no other connections to the internet.
