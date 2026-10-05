<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Hue lights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The kids switch 1–3 Hue scenes of their room on and off from the tablet; parents set it up on a new "Licht" page; the sleep timer can end with a scene or lights off.

**Architecture:** A `muckebox/hue/` package (client, discovery, fake bridge) like `muckebox/sonos/`, a `Lights` service on its own lane in `muckebox/runtime/lights.py`, a `hue` settings section, kids and admin endpoints, and UI in both kids layouts and the parents' page.

**Tech Stack:** Python 3.13, Flask, requests/urllib3 (fingerprint pinning), zeroconf (mDNS), vanilla JS/CSS, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-05-hue-lights-design.md`

## Global Constraints

- Hue API v2 only; HTTPS to the bridge, certificate pinned by SHA-256 fingerprint (trust on first use).
- No Hue cloud (no `discovery.meethue.com`), no Hue account.
- All chosen scenes allowed at any time, also at bedtime.
- Light cooldown 3 s (fixed), group `light`.
- Max 3 slots; pictures from `sun`, `book`, `star`, `moon`, `bulb`.
- The application key and the fingerprint never leave the server (not in any API response or log).
- Nothing about Hue runs on the transport or volume lane; a slow or missing bridge never affects music.
- Automated tests never touch a real bridge.
- UI texts German via `muckebox/i18n/de.py`; code, comments and docs English.

## Review Focus

- The bridge is unreachable at start or later → buttons dimmed, kids taps get `503`, music unaffected (test in Task 4).
- A slot's scene was deleted in the Hue app → the slot is skipped on the tablet, the parents' page says so (test in Task 4).
- The certificate changed → `hue_certificate_changed`, "Neu verbinden" keeps the key (test in Task 1).
- Pairing while the button is not pressed → `409 hue_link_button`, no settings written (test in Task 5).
- The sleep timer ended while Muckebox was restarting → the light action runs once within 15 minutes, never twice (test in Task 4).

---

### Task 1: Hue client and fake bridge

**Files:** Create `muckebox/hue/__init__.py`, `muckebox/hue/model.py`, `muckebox/hue/errors.py`, `muckebox/hue/client.py`, `muckebox/hue/fake.py`; Test `tests/test_hue_client.py`.

**Interfaces (produced):**
- `model.Room(id, name, grouped_light)`, `model.Scene(id, name, room, active: bool)`, `model.BridgeInfo(ip, id, name)`, `model.Pairing(info, key, fingerprint)`.
- `errors.HueError(code)` with subclasses `HueUnreachable` (`hue_unreachable`), `HueLinkButton` (`hue_link_button`), `HueCertificateChanged` (`hue_certificate_changed`), `HueUnauthorized` (`hue_unauthorized`), `HueNotFound` (`hue_not_found`).
- `client.fingerprint_of(ip) -> str`, `client.pair(ip, devicetype) -> Pairing`, `client.HueClient(ip, key, fingerprint)` with `rooms() -> list[Room]`, `scenes() -> list[Scene]`, `recall(scene_id)`, `off(grouped_light_id)`, `info() -> BridgeInfo`.
- `fake.FakeBridge` implements the same methods plus `press_button()`; `fake.fake_pair`.

- [ ] Tests (with a fake HTTP transport injected into `requests.Session`): pairing returns key and pinned fingerprint; error 101 → `HueLinkButton`; reading rooms/scenes maps `scene.group` and `status.active`; `recall`/`off` send the documented PUT bodies; an SSL fingerprint mismatch → `HueCertificateChanged`; connection errors → `HueUnreachable`; 403 → `HueUnauthorized`.
- [ ] Implement with an `HTTPAdapter` whose pool manager uses `assert_fingerprint`; timeouts 3 s.
- [ ] Commit.

### Task 2: Discovery

**Files:** Create `muckebox/hue/discovery.py`; Modify `requirements.in`, lock files via `scripts/lock`; README dependency table; Test `tests/test_hue_discovery.py`.

**Interfaces:** `discovery.find_bridges(ip: str | None, timeout=3.0) -> list[BridgeInfo]` — with an IP: `GET /api/0/config` over HTTPS without verification of the CA (identity only, no secrets sent); without: mDNS browse `_hue._tcp.local.` for `timeout` seconds and read each bridge's config.

- [ ] Tests with injected browser/fetch functions; invalid IPs rejected via `validate_seed_ip`.
- [ ] Commit.

### Task 3: Settings

**Files:** Modify `muckebox/settings.py`; Test `tests/test_settings.py`.

**Interfaces:** `HueSettings(bridge: HueBridge | None, room: str | None, slots: tuple[HueSlot, ...])`, `HueBridge(ip, id, name, key, fingerprint)`, `HueSlot(scene, picture)`; `SleepTimerSettings.lights: str` (`"keep"`, `"off"` or a scene id); `store.set_hue_bridge(bridge | None)`, `store.set_hue(data)` (room and slots), `hue_to_json(settings, secrets=False)`.

- [ ] Tests: defaults, round trip, invalid pictures/too many slots/bad ids → `hue_invalid`, sleep-timer `lights` validation, old files load, the public JSON never contains key or fingerprint.
- [ ] Commit.

### Task 4: Lights runtime

**Files:** Create `muckebox/runtime/lights.py`; Modify `muckebox/runtime/service.py` (own lane, state section, cooldown group), `muckebox/runtime/timers.py` (`lights_done_end`), `muckebox/runtime/cooldown.py` (`LIGHT`, `LIGHT_SECONDS = 3`); Test `tests/test_lights.py`.

**Interfaces:** `Lights(store, keeper, timers, clock, client_factory, lane_factory)` with `toggle(slot: int) -> dict`, `document() -> dict`, `poll()`, `admin_view() -> dict`, `reconnect()`; `Runtime.lights`.

- [ ] Tests: toggle recalls, toggling the active slot turns the room off; cooldown; unreachable bridge → `Unavailable("hue_unreachable")` and `available: false`; deleted scene skipped; sleep-timer end recalls/turns off once, also after a restart within 15 minutes, never after; bedtime does not block lights.
- [ ] Commit.

### Task 5: HTTP API

**Files:** Modify `muckebox/web/api.py`, `muckebox/web/admin.py`, `muckebox/i18n/de.py`, `docs/api.md`; Test `tests/test_api.py`, `tests/test_admin.py`.

- [ ] Kids `POST /api/lights/<slot>/toggle`; admin `GET /api/admin/hue`, `POST /api/admin/hue/search`, `POST /api/admin/hue/pair`, `POST /api/admin/hue/reconnect`, `DELETE /api/admin/hue`, `PUT /api/admin/settings/hue`; sleep-timer `lights`.
- [ ] Tests incl. `409 hue_link_button` writes nothing, secrets never in responses.
- [ ] Commit.

### Task 6: Parents' page "Licht"

**Files:** Modify `muckebox/templates/admin.html`, `muckebox/static/js/admin.js`, `muckebox/static/css/admin.css`, `muckebox/i18n/de.py`; e2e in `tests/e2e/test_browser.py`.

- [ ] Search/IP, pair with 30 s countdown, room, three slots (scene + picture), disconnect, reconnect; sleep-timer "Am Ende"; overview light line.
- [ ] Commit.

### Task 7: Kids light buttons

**Files:** Modify `muckebox/templates/kids.html`, `muckebox/static/js/kids.js`, `muckebox/static/css/kids.css`; add pictures `sun.svg`, `book.svg`, `star.svg`, `bulb.svg` (Twemoji) with `muckebox/assets.py`, `docs/credits.md`; e2e test.

- [ ] Buttons above the pages ("small") / in the header ("big"); active glow, dimmed when unavailable; cooldown; tap uses the forgiving press handling.
- [ ] Commit.

### Task 8: Wiring, demo, docs

**Files:** Modify `muckebox/__main__.py` (real client vs. fake bridge with `MUCKEBOX_FAKE_SONOS=1`), README (feature, setup incl. VLAN/mDNS reflector and firewall TCP 443), PRIVACY.md, SECURITY.md, `docs/architecture.md`.

- [ ] `scripts/check` green; demo screenshots of both layouts and the parents' page.
- [ ] Commit.
