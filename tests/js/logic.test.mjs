// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Unit tests for the kids view logic: `node --test tests/js/`

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PAD_MAX,
  cooldownGroup,
  POLL_MS,
  initial,
  isBedtime,
  isCooling,
  isPlaying,
  limitSegments,
  nextDelay,
  overlayKind,
  overlayTextKey,
  padPress,
  padReady,
  tileTappable,
  placeholderColour,
  moonMode,
  sleepLeft,
  tileState,
  volumeSegments,
} from "../../muckebox/static/js/logic.js";

test("polling backs off after failures and caps at 10 s", () => {
  assert.equal(nextDelay(0), POLL_MS);
  assert.equal(nextDelay(1), 2000);
  assert.equal(nextDelay(2), 4000);
  assert.equal(nextDelay(3), 8000);
  assert.equal(nextDelay(10), 10000);
});

test("overlay: offline wins, then config, then sleeping speaker", () => {
  const state = (status) => ({ sonos: { status } });
  assert.equal(overlayKind(2, state("ok")), "offline");
  assert.equal(overlayKind(1, state("ok")), null);
  assert.equal(overlayKind(0, null), null);
  assert.equal(overlayKind(0, state("not_configured")), "config");
  for (const status of ["sonos_unreachable", "upnp_disabled", "room_not_found"]) {
    assert.equal(overlayKind(0, state(status)), "sleeping");
  }
  assert.equal(overlayKind(0, state("starting")), null);
  assert.equal(overlayTextKey("sleeping"), "kids.sleeping");
});

test("volume bar: full bar means maximum volume", () => {
  assert.equal(volumeSegments(25, 25), 10);
  assert.equal(volumeSegments(0, 25), 0);
  assert.equal(volumeSegments(1, 25), 1); // audible volume is never an empty bar
  assert.equal(volumeSegments(13, 25), 5);
  assert.equal(volumeSegments(40, 25), 10);
  assert.equal(volumeSegments(null, 25), 0);
  assert.equal(volumeSegments(10, 0), 0);
});

test("tile state follows pending, playing and paused", () => {
  const state = {
    pending: null,
    playback: { state: "playing", tile_id: "a" },
  };
  assert.equal(tileState("a", state), "playing");
  assert.equal(tileState("b", state), "");
  assert.equal(tileState("a", { ...state, playback: { state: "paused", tile_id: "a" } }), "paused");
  assert.equal(tileState("b", { ...state, pending: { tile_id: "b" } }), "pending");
  assert.equal(tileState("c", state, "c"), "pending");
  assert.equal(tileState("c", null, "c"), "pending");
  assert.equal(tileState("c", null), "");
  assert.ok(isPlaying({ playback: { state: "transitioning" } }));
  assert.ok(!isPlaying({ playback: { state: "stopped" } }));
  assert.ok(!isPlaying(null));
});

test("placeholders get an initial and a stable colour", () => {
  assert.equal(initial("  über uns"), "Ü");
  assert.equal(initial("3 Fragezeichen"), "3");
  assert.equal(initial(""), "♪");
  assert.equal(placeholderColour("t1"), placeholderColour("t1"));
  assert.match(placeholderColour("t2"), /^#[0-9a-f]{6}$/);
});

test("bedtime: the moon replaces the tiles and hides the sleeping speaker", () => {
  const state = (phase, status = "ok", sleep = null) => ({
    sonos: { status },
    schedule: { phase },
    sleep_timer: { ends_at: sleep },
  });
  assert.equal(isBedtime(state("closed")), true);
  assert.equal(isBedtime(state("fading")), false);
  assert.equal(isBedtime(null), false);
  assert.equal(overlayKind(0, state("closed", "sonos_unreachable")), null);
  assert.equal(overlayKind(0, state("open", "sonos_unreachable")), "sleeping");
  assert.equal(overlayKind(2, state("closed")), "offline");
});

test("moon button: start, running, fading or hidden", () => {
  const state = (phase, timer) => ({ schedule: { phase }, sleep_timer: timer });
  assert.equal(moonMode(state("open", { enabled: true, ends_at: null })), "start");
  assert.equal(moonMode(state("open", { enabled: false, ends_at: null })), "hidden");
  assert.equal(moonMode(state("open", { enabled: true, ends_at: 1790000000 })), "running");
  assert.equal(moonMode(state("fading", { enabled: false, ends_at: null })), "fading");
  assert.equal(moonMode(state("closed", { enabled: true, ends_at: null })), "hidden");
  assert.equal(moonMode(null), "hidden");
});

test("sleep timer ring shows the time left", () => {
  const ends = 1_790_001_800; // 30 minutes after the start below
  assert.equal(sleepLeft(ends, 30, 1_790_000_000_000), 1);
  assert.equal(sleepLeft(ends, 30, 1_790_000_900_000), 0.5);
  assert.equal(sleepLeft(ends, 30, 1_790_002_000_000), 0);
  assert.equal(sleepLeft(null, 30, 0), 0);
});

test("volume bar: segments above the limit are dimmed", () => {
  assert.equal(limitSegments(25, 25), 10);
  assert.equal(limitSegments(12, 25), 5);
  assert.equal(limitSegments(1, 25), 1);
  assert.equal(limitSegments(undefined, 25), 10);
});

test("PIN pad: digits only, a delete key, a minimum length", () => {
  let pin = "";
  for (const key of ["1", "x", "2", "back", "3", "4", "5"]) pin = padPress(pin, key);
  assert.equal(pin, "1345");
  assert.equal(padReady("123"), false);
  assert.equal(padReady(pin), true);
  assert.equal(padPress("9".repeat(PAD_MAX), "1").length, PAD_MAX);
});

test("anti disco: the tap groups wait, the loaded tile can always be resumed", () => {
  assert.equal(cooldownGroup("next"), "skip");
  assert.equal(cooldownGroup("previous"), "skip");
  assert.equal(cooldownGroup("play"), "toggle");
  assert.equal(cooldownGroup("pause"), "toggle");
  const cooldowns = { tile: 5000 };
  assert.equal(isCooling(cooldowns, "tile", 4999), true);
  assert.equal(isCooling(cooldowns, "tile", 5000), false);
  assert.equal(isCooling(cooldowns, "skip", 0), false);
  const state = { playback: { state: "paused", tile_id: "ta" } };
  assert.equal(tileTappable("ta", state, cooldowns, 1000), true);
  assert.equal(tileTappable("tb", state, cooldowns, 1000), false);
  assert.equal(tileTappable("tb", state, cooldowns, 6000), true);
  assert.equal(tileTappable("tb", null, {}, 0), true);
});
