<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Hardware checklist

Automated tests run against a simulated speaker and in browser engines.
These checks need a real Sonos system and real tablets. Tick them off when
a release is tried at home, and report anything that differs.

## Sonos

- [x] Room search with and without a speaker address; choosing a room.
- [x] Radio, Apple Music albums and playlists, and a NAS library item start
      from their tiles; the volume limit corrects the volume within 2 s.
- [ ] Weiterhören: an Apple Music album and a NAS album resume at the saved
      track and second; after the last track the album starts from the
      beginning next time.
- [ ] End of the usage time: the fade sounds smooth (1-step changes) and the
      music pauses once; TuneIn radio stops or pauses; the volume is back
      afterwards.
- [ ] A grouped kids room with other music is only faded, not paused.
- [ ] Freeze dance: mute and unmute take effect quickly; with a stereo pair
      or a Sub; in a group only the kids room is silent.
- [ ] Fixed-volume products (line out): the limit and fading have no effect;
      the parents' status says so.

## Server

- [ ] The Docker image knows the time zone (`TZ` set or taken over on the
      parents' page); the clock on the parents' page is right after a NAS
      restart.

## iPad (iPadOS 16.4 or newer, home-screen app with Guided Access)

- [ ] A German voice speaks in the games; sounds play after the first tap.
- [ ] The mute switch does not silence the games; the volume buttons do.
- [ ] Holding the moon for 3 seconds opens the PIN pad under Guided Access,
      without the text-selection magnifier.
- [ ] Display Auto-Lock does not interrupt the breathing exercise.

## Android

- [ ] Chrome: voice and sounds in the games.
- [ ] Fully Kiosk Browser: sounds with *Autoplay Audio*; voice through
      Fully's text to speech (*JavaScript Interface* enabled).
