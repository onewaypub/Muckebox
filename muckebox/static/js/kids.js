// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Kids view: shows the tiles, polls the state, sends taps to the server.

import { ApiError, get, post } from "./api.js";
import { initBedtime, padIsOpen, renderBedtime } from "./bedtime.js";
import { gameIsOpen, initGames, renderGames } from "./games.js";
import { loadMessages, t, translatePage } from "./i18n.js";
import {
  initial,
  isBedtime,
  isPlaying,
  limitSegments,
  nextDelay,
  overlayKind,
  overlayTextKey,
  placeholderColour,
  tileState,
  volumeSegments,
} from "./logic.js";

const TOAST_MS = 4000;
const VOLUME_SEGMENTS = 10;

const view = {
  tiles: document.getElementById("tiles"),
  empty: document.getElementById("empty"),
  template: document.getElementById("tile-template"),
  toggle: document.getElementById("toggle"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
  louder: document.getElementById("louder"),
  quieter: document.getElementById("quieter"),
  volume: document.getElementById("volume"),
  overlay: document.getElementById("overlay"),
  overlayText: document.getElementById("overlay-text"),
  toast: document.getElementById("toast"),
  toastText: document.getElementById("toast-text"),
};

const model = {
  state: null,
  etag: null,
  libraryRev: null,
  tileCount: 0,
  failures: 0,
  localPending: null,
  shownErrorAt: null,
  pollTimer: null,
  toastTimer: null,
};

// -- rendering ------------------------------------------------------------------

function renderTiles(tiles) {
  view.tiles.replaceChildren();
  for (const tile of tiles) {
    const node = view.template.content.firstElementChild.cloneNode(true);
    node.dataset.id = tile.id;
    node.setAttribute("aria-label", tile.title);
    node.querySelector(".title").textContent = tile.title;
    const cover = node.querySelector(".cover");
    if (tile.cover) {
      loadCover(node, cover, tile);
    } else {
      showPlaceholder(node, tile);
    }
    node.addEventListener("click", () => playTile(tile.id));
    view.tiles.append(node);
  }
  model.tileCount = tiles.length;
  renderState();
}

// A cover that fails to load (e.g. while the Wi-Fi reconnects) is tried
// again a few times; meanwhile the tile shows its letter.
const COVER_RETRIES = 6;

function loadCover(node, cover, tile) {
  let tries = 0;
  cover.addEventListener("load", () => showCover(node));
  cover.addEventListener("error", () => {
    showPlaceholder(node, tile);
    if (tries >= COVER_RETRIES) return;
    const delay = Math.min(60000, 5000 * 2 ** tries++);
    setTimeout(() => {
      if (!node.isConnected) return; // the tiles were rebuilt meanwhile
      cover.loading = "eager";
      cover.src = `${tile.cover}?retry=${tries}`;
    }, delay);
  });
  cover.src = tile.cover;
}

function showCover(node) {
  const cover = node.querySelector(".cover");
  cover.hidden = false;
  node.classList.remove("no-cover");
  node.style.background = "";
  node.querySelector(".placeholder").textContent = "";
}

function showPlaceholder(node, tile) {
  node.querySelector(".cover").hidden = true;
  node.classList.add("no-cover");
  node.style.background = placeholderColour(tile.id);
  node.querySelector(".placeholder").textContent = initial(tile.title);
}

function renderState() {
  const state = model.state;
  for (const node of view.tiles.children) {
    const current = tileState(node.dataset.id, state, model.localPending);
    node.classList.toggle("playing", current === "playing" || current === "paused");
    node.classList.toggle("paused", current === "paused");
    node.classList.toggle("pending", current === "pending");
  }
  const locked = Boolean((state && state.pending) || model.localPending);
  view.tiles.classList.toggle("locked", locked);

  const playing = isPlaying(state);
  view.toggle.classList.toggle("is-playing", playing);
  view.toggle.setAttribute("aria-label", t(playing ? "kids.pause" : "kids.play"));
  const playback = state ? state.playback : null;
  view.toggle.disabled = !playback || (!playback.can_toggle && !playing);
  view.prev.disabled = !playback || !playback.can_prev;
  view.next.disabled = !playback || !playback.can_next;
  const bedtime = renderBedtime(state);
  renderGames(state);
  view.tiles.hidden = bedtime;
  view.empty.hidden = bedtime || model.tileCount > 0;
  renderVolume(state ? state.volume : null, bedtime);
  renderOverlay();
  renderError(state);
}

function renderVolume(volume, bedtime = isBedtime(model.state)) {
  if (view.volume.children.length !== VOLUME_SEGMENTS) {
    view.volume.replaceChildren(
      ...Array.from({ length: VOLUME_SEGMENTS }, (_, i) => {
        const segment = document.createElement("i");
        segment.style.height = `${30 + (70 * (i + 1)) / VOLUME_SEGMENTS}%`;
        return segment;
      }),
    );
  }
  const lit = volume ? volumeSegments(volume.value, volume.max, VOLUME_SEGMENTS) : 0;
  const allowed = volume ? limitSegments(volume.limit, volume.max, VOLUME_SEGMENTS) : VOLUME_SEGMENTS;
  [...view.volume.children].forEach((segment, index) => {
    segment.classList.toggle("on", index < lit);
    segment.classList.toggle("over", index >= allowed); // above the limit while fading
  });
  if (volume) {
    view.volume.setAttribute("aria-valuemax", String(volume.max));
    view.volume.setAttribute("aria-valuenow", String(volume.value ?? 0));
  }
  const known = Boolean(volume && volume.value !== null);
  view.louder.disabled = !known || bedtime || volume.value >= (volume.limit ?? volume.max);
  view.quieter.disabled = !known || volume.value <= 0;
}

function renderOverlay() {
  const kind = overlayKind(model.failures, model.state);
  view.overlay.hidden = !kind;
  if (kind) {
    view.overlay.dataset.kind = kind;
    view.overlayText.textContent = t(overlayTextKey(kind));
  }
}

function renderError(state) {
  const error = state && state.last_error;
  if (error && error.at !== model.shownErrorAt) {
    model.shownErrorAt = error.at;
    showToast(t("kids.error"));
  }
}

function showToast(text) {
  view.toastText.textContent = text;
  view.toast.hidden = false;
  clearTimeout(model.toastTimer);
  model.toastTimer = setTimeout(() => {
    view.toast.hidden = true;
  }, TOAST_MS);
}

// -- server communication ---------------------------------------------------------

async function loadTiles() {
  const { data } = await get("/api/tiles");
  model.libraryRev = data.rev;
  renderTiles(data.tiles);
}

async function poll() {
  clearTimeout(model.pollTimer);
  try {
    const response = await get("/api/state", { etag: model.etag });
    model.failures = 0;
    if (response.status !== 304) {
      model.etag = response.etag;
      applyState(response.data);
    } else {
      renderOverlay();
    }
    if (model.state && model.state.library_rev !== model.libraryRev) {
      await loadTiles();
    }
  } catch {
    model.failures += 1;
    renderOverlay();
  }
  model.pollTimer = setTimeout(poll, nextDelay(model.failures));
}

// The build of the page itself; the server reports its current one.
const PAGE_ASSETS = document.body.dataset.assetVersion;

function applyState(state) {
  if (state.assets && PAGE_ASSETS && state.assets !== PAGE_ASSETS && !padIsOpen() && !gameIsOpen()) {
    // Muckebox was updated: load the new page (only after a successful answer,
    // and not while a parent types the PIN).
    window.location.reload();
    return;
  }
  model.state = state;
  if (!state.pending) model.localPending = null;
  renderState();
}

function pollSoon() {
  clearTimeout(model.pollTimer);
  model.pollTimer = setTimeout(poll, 150);
}

async function command(action) {
  try {
    const { data } = await action();
    return data;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return null; // busy: ignore
    if (error instanceof ApiError && error.status === 503) {
      pollSoon();
      return null;
    }
    showToast(t("kids.error"));
    return null;
  } finally {
    pollSoon();
  }
}

// -- user actions ----------------------------------------------------------------

function playTile(id) {
  const state = model.state;
  if (model.localPending || (state && state.pending)) return;
  model.localPending = id;
  renderState();
  command(() => post(`/api/tiles/${encodeURIComponent(id)}/play`)).then((data) => {
    if (!data || data.result !== "accepted") model.localPending = null;
    renderState();
  });
}

function transport(action) {
  command(() => post(`/api/transport/${action}`)).then((data) => {
    if (data && data.playback && model.state) {
      model.state.playback = data.playback;
      renderState();
    }
  });
}

// Send what the button shows: a double tap then cannot undo the first tap.
function togglePlayback() {
  transport(isPlaying(model.state) ? "pause" : "play");
}

async function changeVolume(direction) {
  const data = await command(() => post(`/api/volume/${direction}`));
  if (data && model.state) {
    model.state.volume = data.volume;
    renderVolume(data.volume);
  }
}

function bindControls() {
  view.toggle.addEventListener("click", togglePlayback);
  view.prev.addEventListener("click", () => transport("previous"));
  view.next.addEventListener("click", () => transport("next"));
  view.louder.addEventListener("click", () => changeVolume("up"));
  view.quieter.addEventListener("click", () => changeVolume("down"));
  // Come back quickly after the tablet wakes up or the network returns.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") pollSoon();
  });
  window.addEventListener("pageshow", pollSoon);
  window.addEventListener("online", pollSoon);
  // No context menus, no pinch zoom.
  document.addEventListener("contextmenu", (event) => event.preventDefault());
  document.addEventListener("gesturestart", (event) => event.preventDefault());
}

async function start() {
  loadMessages();
  translatePage();
  bindControls();
  initBedtime({ onChange: pollSoon });
  initGames({ onChange: pollSoon, onError: () => showToast(t("kids.error")) });
  renderState();
  try {
    await loadTiles();
  } catch {
    model.failures = 2;
    renderOverlay();
  }
  poll();
}

start();
