// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Unit tests for the kids view logic: `node --test tests/js/`

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  POLL_MS,
  initial,
  isPlaying,
  nextDelay,
  overlayKind,
  overlayTextKey,
  placeholderColour,
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
