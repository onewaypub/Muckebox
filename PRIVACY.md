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
| `covers/` | Cover pictures: album art and pictures uploaded by parents. Uploaded photos are re-encoded; their metadata (EXIF, e.g. location) is removed. | Only if parents upload photos of people. |
| `secret_key` | A random key that signs the parents' login cookie. | No |
| `state.json` | Which tile Muckebox started last. | No |

Muckebox stores **nothing about the children**: no usage history, no
listening statistics, no names.

To delete everything, stop the container and delete the data folder.

## Cookies and logs

- **Cookies:** the kids view uses none. The parents' page sets one cookie
  after a successful PIN login (`muckebox_admin`). It is technically necessary
  for the login, expires after 12 hours and is deleted on logout.
- **Rate limiting:** failed PIN attempts are counted per client IP address in
  memory for at most 15 minutes. They are never written to disk.
- **Logs:** the container log contains technical messages (for example the
  Sonos room name, speaker addresses, errors). It contains no PINs and no
  request logs with client addresses.

## Connections

| Connection | When | What is sent |
|---|---|---|
| Tablet/phone → Muckebox | While the pages are open | Taps and page requests, within your home network. The pages load nothing from third parties: no web fonts, no CDNs, no trackers. |
| Muckebox → Sonos speakers | Continuously while running | Control commands and status queries within your home network. |
| Sonos speakers → music services | When music plays | Sonos itself streams from the services you linked in the Sonos app, under their terms. Muckebox only tells the speaker what to play. |
| Muckebox → album art servers | When parents open or add Sonos favorites | Album art addresses provided by Sonos; some point to the music services' image servers on the internet. |
| Muckebox → Apple Music, Spotify, TIDAL, Deezer | Only when parents add a share link | The link itself, to look up its title and cover (oEmbed or the public web page). These services see the public IP address of your internet connection. |

Muckebox makes no other connections to the internet.
