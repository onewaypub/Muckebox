// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Pure functions of the kids view (tested with `node --test`).

export const POLL_MS = 1500;
const MAX_BACKOFF_MS = 10000;
const SLEEPING = new Set(["sonos_unreachable", "upnp_disabled", "room_not_found"]);

/** Delay before the next state poll, backing off after failures. */
export function nextDelay(failures) {
  if (failures <= 0) return POLL_MS;
  return Math.min(MAX_BACKOFF_MS, 2000 * 2 ** (failures - 1));
}

/**
 * Which full-screen overlay to show, if any.
 * "offline": the Muckebox server does not answer;
 * "config": Muckebox is not set up; "sleeping": the speaker is not reachable.
 */
export function overlayKind(serverFailures, state) {
  if (serverFailures >= 2) return "offline";
  if (!state) return null;
  const status = state.sonos && state.sonos.status;
  if (status === "not_configured") return "config";
  if (SLEEPING.has(status)) return "sleeping";
  return null;
}

export function overlayTextKey(kind) {
  return { offline: "kids.offline", config: "kids.config", sleeping: "kids.sleeping" }[kind];
}

/** Number of lit segments in the volume bar; full bar = maximum volume. */
export function volumeSegments(value, max, count = 10) {
  if (value === null || value === undefined || !max) return 0;
  const ratio = Math.max(0, Math.min(1, value / max));
  return value > 0 ? Math.max(1, Math.round(ratio * count)) : 0;
}

export function isPlaying(state) {
  const playback = state && state.playback;
  return Boolean(playback) && ["playing", "transitioning"].includes(playback.state);
}

/** CSS state of a tile: "playing", "paused", "pending" or "". */
export function tileState(tileId, state, localPendingId = null) {
  if (!state) return localPendingId === tileId ? "pending" : "";
  const pending = state.pending ? state.pending.tile_id : localPendingId;
  if (pending === tileId) return "pending";
  if (state.playback && state.playback.tile_id === tileId) {
    return isPlaying(state) ? "playing" : "paused";
  }
  return "";
}

/** First letter of a title, for tiles without a cover. */
export function initial(title) {
  const match = String(title || "").trim().match(/\p{L}|\p{N}/u);
  return match ? match[0].toLocaleUpperCase() : "♪";
}

/** A stable, friendly colour for a tile without a cover. */
export function placeholderColour(id) {
  const colours = ["#e76f51", "#2a9d8f", "#e9c46a", "#f4a261", "#8ab17d", "#9b5de5", "#00bbf9"];
  let hash = 0;
  for (const char of String(id)) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return colours[hash % colours.length];
}
