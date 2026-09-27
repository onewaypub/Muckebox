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
  // At bedtime the moon says enough, even if the speaker sleeps too.
  if (SLEEPING.has(status) && !isBedtime(state)) return "sleeping";
  return null;
}

/** The usage time is over: the moon replaces the tiles. */
export function isBedtime(state) {
  return Boolean(state && state.schedule && state.schedule.phase === "closed");
}

/** The small moon in the controls: while the music fades or the sleep timer runs. */
export function showsSmallMoon(state) {
  if (!state || isBedtime(state)) return false;
  const fading = Boolean(state.schedule && state.schedule.phase === "fading");
  const timer = Boolean(state.sleep_timer && state.sleep_timer.ends_at);
  return fading || timer;
}

// -- the parents' PIN pad on the kids tablet --------------------------------------------

export const PAD_MIN = 4;
export const PAD_MAX = 12;
/** Wrong PINs in a row before the long press is locked for a while. */
export const PAD_TRIES = 3;

/** The PIN after pressing ``key`` ("0"-"9" or "back"). */
export function padPress(pin, key) {
  if (key === "back") return pin.slice(0, -1);
  if (/^[0-9]$/.test(key) && pin.length < PAD_MAX) return pin + key;
  return pin;
}

export function padReady(pin) {
  return pin.length >= PAD_MIN;
}

export function overlayTextKey(kind) {
  return { offline: "kids.offline", config: "kids.config", sleeping: "kids.sleeping" }[kind];
}

/** Segments above the current limit (e.g. while fading) are shown dimmed. */
export function limitSegments(limit, max, count = 10) {
  if (limit === null || limit === undefined || !max) return count;
  return Math.max(1, Math.round(Math.max(0, Math.min(1, limit / max)) * count));
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
