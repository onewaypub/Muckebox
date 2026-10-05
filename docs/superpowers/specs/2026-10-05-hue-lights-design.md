<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Hue lights on the kids tablet — design

Status: agreed with the maintainer on 2026-10-05.

## Goal

The kids switch 1–3 Philips Hue scenes of their room from the tablet, on and
off. Parents choose the room, the scenes and their pictures. Everything stays
in the home network: no Hue cloud, no Hue account.

## Decisions

- Requires a Hue Bridge (square bridge, API v2). Bluetooth-only lamps are out
  of scope.
- **All chosen scenes stay allowed at any time**, also at bedtime (usage times
  and the sleep lock do not lock the light buttons).
- The **sleep timer** gets an option for its end: *nothing* (default), *a
  scene* (e.g. "Nachtlicht") or *lights off*.
- The pause without taps (`idle_minutes`) never touches the lights.
- **Anti disco:** after a light tap all light buttons wait 3 s (fixed).
- Works generally, including the bridge in another VLAN:
  - automatic search via mDNS (`_hue._tcp`), which works wherever the Hue app
    finds the bridge (same network, or an mDNS reflector/repeater between
    VLANs);
  - entering the bridge's IP address by hand always works if the NAS may
    reach it on TCP 443;
  - the Hue cloud discovery (`discovery.meethue.com`) is not used.

## Bridge connection (`muckebox/hue/`)

- `HueBackend` protocol, `HueClient` (HTTPS, `requests`) and `FakeBridge`
  (tests and demo mode, `MUCKEBOX_FAKE_SONOS=1` also enables it), mirroring
  `muckebox/sonos/`.
- **Pairing:** `POST https://<ip>/api` with
  `{"devicetype": "muckebox#<host>", "generateclientkey": true}`. Error 101
  ("link button not pressed") is expected while the parents walk to the
  bridge; the parents' page retries for 30 s with a countdown. The result's
  `username` is the application key.
- **Identity:** `GET https://<ip>/api/0/config` (no key needed) gives the
  bridge id and name.
- **TLS:** the bridge's certificate must name the bridge id (CN). It is
  accepted if it chains to the Hue root CA shipped with Muckebox; otherwise
  (older firmware with a self-signed certificate) its SHA-256 fingerprint is
  pinned at pairing (trust on first use). A changed certificate is refused
  with a clear message ("neu verbinden").
- **Reading:** `GET /clip/v2/resource/room`, `/scene`, `/grouped_light` with
  the header `hue-application-key`. A scene belongs to a room via
  `scene.group`; it is active when `scene.status.active` is not `inactive`.
- **Switching:** scene on: `PUT /clip/v2/resource/scene/<id>`
  `{"recall": {"action": "active"}}`; lights off:
  `PUT /clip/v2/resource/grouped_light/<room's grouped_light>`
  `{"on": {"on": false}}`.
- **Errors:** connection problems open a circuit breaker (like Sonos); the
  buttons are dimmed meanwhile; music is never affected.

## Runtime

- A separate **light lane**: switches and a poll every 5 s (active scene, so
  changes from the Hue app or a wall switch show on the tablet). Nothing
  about Hue runs on the transport or volume lane.
- **Kids command:** tapping a slot recalls its scene; tapping the active slot
  turns the room off. Cooldown group `light`, 3 s.
- **Sleep timer end:** when the sleep timer's end is handled (the existing
  "pause once" path), the light lane recalls the chosen scene or turns the
  room off — once per end.
- State document, section `lights`:
  `{"available": bool, "slots": [{"slot": 1, "picture": "moon", "active": bool}], "cooldown": 3}`;
  empty `slots` when Hue is not set up.

## Settings (`settings.json`, section `hue`)

`{"bridge": {"ip", "id", "name", "key", "fingerprint"}, "room": "<room id>",
"slots": [{"scene": "<scene id>", "picture": "sun"}], ...}` and in
`sleep_timer`: `"lights": "keep" | "off" | "<scene id>"`. The key and the
fingerprint are never returned by the API. Old files without the section load
unchanged.

## API

Kids (no login): `POST /api/lights/<slot>/toggle` → `200 {"lights": …}`,
`409 cooling_down`, `503 hue_unreachable`.

Parents (login): `POST /api/admin/hue/search` (mDNS and/or the given IP),
`POST /api/admin/hue/pair {"ip"}` (`409 hue_link_button` until the button is
pressed), `DELETE /api/admin/hue` (forget the bridge),
`GET /api/admin/hue` (bridge, rooms, the chosen room's scenes, slots),
`PUT /api/admin/settings/hue {"room", "slots"}`.

## Parents' page

New page **"Licht"**: search or enter the bridge's IP, "Verbinden" with a
30 s countdown ("Jetzt den Knopf auf der Bridge drücken"), choose the room,
up to three slots (scene + picture), "Bridge trennen". The sleep-timer page
gets "Am Ende: nichts / Szene … / Licht aus". The overview shows the light
status.

## Kids view

- "small": up to three big round light buttons above the tiles (the pages
  get a little less height).
- "big": round light buttons in the header next to games and the moon.
- The active one glows; unavailable buttons are dimmed.
- Pictures: sun, book, star, moon, light bulb (Twemoji, CC BY 4.0; sun, book,
  star and bulb are added).

## Privacy and security

- Only the home network: Muckebox ↔ bridge. PRIVACY.md lists what is stored
  (bridge address, id, name, key, fingerprint, chosen room and scenes).
- SECURITY.md: the key controls all lights of the household, so it stays in
  `settings.json` (mode 0600) and never leaves the server.
- New dependency `zeroconf` (mDNS search only).

## Tests

Unit tests with `FakeBridge` and a fake clock (pairing incl. error 101,
TLS check incl. fingerprint pinning, reading rooms/scenes, toggle and off,
cooldown, breaker, sleep-timer end, settings validation), API tests, an e2e
test (light buttons in both layouts), demo mode with a simulated bridge.
Real hardware only by hand, never in automated tests.

## Out of scope

Brightness and colours from the tablet, more than three slots, lights for
the games, Hue Entertainment, the Hue cloud, bridges of API v1 only.
