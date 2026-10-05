// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Kids view: shows the tiles, polls the state, sends taps to the server.
//
// Two layouts, chosen by the parents: "small" (0-6 years: six big tiles per
// page, three big buttons) and "big" (7-14 years: titles, what is playing
// with its chapter and progress, a denser grid). The buttons exist once and
// are moved into the slots of the current layout.

import { ApiError, get, post } from "./api.js";
import { initBedtime, padIsOpen, renderBedtime } from "./bedtime.js";
import { hop, plop, setTapSound, unlockFeedback } from "./feedback.js";
import { gameIsOpen, initGames, renderGames } from "./games.js";
import { loadMessages, t, translatePage } from "./i18n.js";
import { diagNote, setTouchDiag, targetName } from "./touchdiag.js";
import {
  cooldownGroup,
  formatTime,
  isBedtime,
  isCooling,
  isPlaying,
  limitSegments,
  nextDelay,
  overlayKind,
  overlayTextKey,
  pageOf,
  paginate,
  placeholderColour,
  placeholderPicture,
  tileState,
  tileTappable,
  trackSeconds,
  volumeSegments,
} from "./logic.js";

const TOAST_MS = 4000;
const DOTS = 5; // volume of the "small" layout: five growing dots
// Taps of small children: the finger may stay down as long as it likes and
// wobble this far (px); only a clear sideways move is a swipe.
const TAP_SLOP = 40;
const SWIPE = 80;
// A finger that rests this long (ms) starts the tile without being lifted.
const HOLD_MS = 250;
const PICTURES = "/static/pictures";
const $ = (id) => document.getElementById(id);

const view = {
  body: document.body,
  tiles: $("tiles"),
  empty: $("empty"),
  template: $("tile-template"),
  toggle: $("toggle"),
  prev: $("prev"),
  next: $("next"),
  louder: $("louder"),
  quieter: $("quieter"),
  volume: $("volume"),
  moon: $("small-moon"),
  games: $("games-button"),
  pagePrev: $("page-prev"),
  pageNext: $("page-next"),
  pageDots: $("page-dots"),
  lights: $("lights"),
  pager: document.querySelector(".pager"),
  playing: $("playing"),
  playingArt: $("playing-art"),
  playingCover: $("playing-cover"),
  playingPicture: $("playing-picture"),
  home: $("home"),
  now: {
    cover: $("now-cover"),
    picture: $("now-picture"),
    art: $("now-art"),
    label: $("now-label"),
    title: $("now-title"),
    sub: $("now-sub"),
    progress: $("now-progress"),
    bar: $("now-bar"),
    position: $("now-position"),
    duration: $("now-duration"),
  },
  overlay: $("overlay"),
  overlayText: $("overlay-text"),
  toast: $("toast"),
  toastText: $("toast-text"),
};

const model = {
  state: null,
  etag: null,
  libraryRev: null,
  tiles: [],
  pages: [],
  page: 0,
  shownPlaying: null, // the playing tile the pages last turned to
  browsing: false, // "small": the house was tapped, the tiles show although one plays
  failures: 0,
  localPending: null,
  // "Anti disco": until when (performance.now()) taps of a group wait.
  cooldowns: {},
  track: null, // the last track reading and when it arrived
  trackReceived: 0,
  shownErrorAt: null,
  pollTimer: null,
  toastTimer: null,
};

const profile = () => view.body.dataset.profile;

// -- layout ---------------------------------------------------------------------

function applyLayout(state) {
  const wanted = (state && state.view) || {};
  if (wanted.profile && wanted.profile !== profile()) view.body.dataset.profile = wanted.profile;
  if (wanted.skip_buttons !== undefined) view.body.dataset.skip = wanted.skip_buttons ? "on" : "off";
  const big = profile() === "big";
  const target = big
    ? { left: $("head-slot"), play: $("now-buttons"), volume: $("now-volume") }
    : { left: $("bar-left"), play: $("bar-play"), volume: $("bar-volume") };
  if (view.toggle.parentElement === target.play) return;
  // The light buttons: in the header ("big"), above the pages ("small").
  if (big) $("head-slot").prepend(view.lights);
  else document.querySelector(".pager").before(view.lights);
  target.left.append(view.moon, view.games);
  target.play.append(view.prev, view.toggle, view.next);
  target.volume.append(view.quieter, view.volume, view.louder);
  view.volume.replaceChildren(); // rebuilt for the layout by renderVolume
  renderTiles(model.tiles);
}

// -- tiles --------------------------------------------------------------------------

function renderTiles(tiles) {
  model.tiles = tiles;
  model.pages = paginate(tiles);
  const pages = model.pages.map((page) => {
    const node = document.createElement("div");
    node.className = "page";
    node.append(...page.map(tileNode));
    return node;
  });
  view.tiles.replaceChildren(...pages);
  view.pageDots.replaceChildren(...model.pages.map(() => document.createElement("i")));
  model.page = Math.min(model.page, Math.max(0, model.pages.length - 1));
  model.shownPlaying = null;
  scrollToPage(model.page, false);
  renderState();
}

function tileNode(tile) {
  const node = view.template.content.firstElementChild.cloneNode(true);
  node.dataset.id = tile.id;
  node.setAttribute("aria-label", tile.title);
  node.querySelector(".title").textContent = tile.title;
  node.querySelector(".art").style.background = placeholderColour(tile.id);
  node.querySelector(".picture").src = `${PICTURES}/${placeholderPicture(tile.id)}.svg`;
  if (tile.cover) {
    loadCover(node, tile);
  } else {
    node.querySelector(".cover").remove();
  }
  // Touch and mouse are handled by the press below; a click without a
  // pointer press (detail 0) comes from the keyboard.
  node.addEventListener("click", (event) => {
    if (event.detail === 0) playTile(tile.id);
  });
  return node;
}

// A cover that fails to load (e.g. while the Wi-Fi reconnects) is tried
// again a few times; meanwhile the tile shows its picture.
const COVER_RETRIES = 6;

function loadCover(node, tile) {
  const cover = node.querySelector(".cover");
  let tries = 0;
  // The cover stays invisible (not hidden: a hidden lazy image never loads)
  // until it is there; until then the tile shows its picture.
  cover.addEventListener("load", () => node.classList.add("has-cover"));
  cover.addEventListener("error", () => {
    node.classList.remove("has-cover");
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

// -- pages ("small") ----------------------------------------------------------------

function scrollToPage(index, smooth = true) {
  const count = model.pages.length;
  model.page = Math.max(0, Math.min(index, count - 1));
  if (profile() === "small" && view.tiles.clientWidth) {
    view.tiles.scrollTo({ left: model.page * view.tiles.clientWidth, behavior: smooth ? "smooth" : "auto" });
  }
  renderPager();
}

function renderPager() {
  const count = model.pages.length;
  const paged = profile() === "small" && count > 1 && !isBedtime(model.state);
  view.pagePrev.hidden = !paged;
  view.pageNext.hidden = !paged;
  view.pageDots.hidden = !paged;
  view.pagePrev.disabled = model.page <= 0;
  view.pageNext.disabled = model.page >= count - 1;
  [...view.pageDots.children].forEach((dot, index) => dot.classList.toggle("on", index === model.page));
}

// -- presses: our own tap and swipe ----------------------------------------------------
//
// The browser's "click" is made for adults: it is dropped after a long press
// (Android treats it as a long-press gesture), when the finger moves a few
// pixels, and between two tiles. Small children press long, roll their finger
// and hit the edge, so the tiles recognise a tap themselves: as soon as the
// finger has rested for HOLD_MS (it need not be lifted), or when it is lifted
// earlier, as long as it moved less than TAP_SLOP. A move beyond that first
// is a swipe (or the "big" grid scrolling) and starts nothing.

let press = null;

function onPressStart(event) {
  if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
  const node = event.target.closest(".tile");
  endPress();
  press = {
    id: node ? node.dataset.id : null,
    node,
    x: event.clientX,
    y: event.clientY,
    moved: 0,
    at: performance.now(),
    target: targetName(event.target),
  };
  if (node) {
    node.classList.add("pressed");
    unlockFeedback(); // inside the touch: tablets allow sound only then
    press.timer = setTimeout(() => firePress("tile-hold"), HOLD_MS);
  }
  // The finger may slide off the tile: its release still belongs to this press.
  view.tiles.setPointerCapture(event.pointerId);
}

function onPressMove(event) {
  if (!press || !event.isPrimary) return;
  const moved = Math.hypot(event.clientX - press.x, event.clientY - press.y);
  if (moved >= TAP_SLOP && press.moved < TAP_SLOP) {
    clearTimeout(press.timer); // a swipe, not a tap
    note("press-moved", moved);
  }
  press.moved = Math.max(press.moved, moved);
}

function note(type, moved = press.moved) {
  diagNote(type, { ms: Math.round(performance.now() - press.at), moved: Math.round(moved), target: press.target });
}

// The tile starts once per press: after resting, or on lifting the finger.
function firePress(how) {
  if (!press || !press.id || press.fired || press.moved >= TAP_SLOP || !press.node.isConnected) return;
  press.fired = true;
  note(how);
  plop();
  hop(press.node.querySelector(".art"));
  playTile(press.id);
}

function onPressEnd(event) {
  if (!press || !event.isPrimary) return;
  const dx = event.clientX - press.x;
  const dy = event.clientY - press.y;
  press.moved = Math.max(press.moved, Math.hypot(dx, dy));
  if (profile() === "small" && Math.abs(dx) >= SWIPE && Math.abs(dx) > Math.abs(dy)) {
    note("press-swipe");
    scrollToPage(model.page + (dx < 0 ? 1 : -1));
  } else {
    firePress("tile-release");
  }
  endPress();
}

function cancelPress() {
  if (press && !press.fired) note("press-cancel");
  endPress();
}

function endPress() {
  if (!press) return;
  clearTimeout(press.timer);
  if (press.node) press.node.classList.remove("pressed");
  press = null;
}

function onScroll() {
  if (profile() !== "small" || !view.tiles.clientWidth) return;
  const page = Math.round(view.tiles.scrollLeft / view.tiles.clientWidth);
  if (page !== model.page) {
    model.page = page;
    renderPager();
  }
}

// Turn to the playing tile once, when it starts (not while the kid browses).
function followPlaying(state) {
  const id = state && state.playback ? state.playback.tile_id : null;
  if (!id || id === model.shownPlaying) return;
  model.shownPlaying = id;
  model.browsing = false; // started elsewhere (e.g. a game): show it big
  const page = pageOf(model.pages, id);
  if (page >= 0 && page !== model.page) scrollToPage(page);
}

// -- state ---------------------------------------------------------------------------

function renderState() {
  const state = model.state;
  applyLayout(state);
  const progress = (state && state.progress) || {};
  for (const node of view.tiles.querySelectorAll(".tile")) {
    const id = node.dataset.id;
    const current = tileState(id, state, model.localPending);
    node.classList.toggle("playing", current === "playing" || current === "paused");
    node.classList.toggle("paused", current === "paused");
    node.classList.toggle("pending", current === "pending");
    const bar = node.querySelector(".progress");
    bar.hidden = !(id in progress) || progress[id] <= 0;
    bar.firstElementChild.style.width = `${Math.round((progress[id] || 0) * 100)}%`;
  }
  const locked = Boolean((state && state.pending) || model.localPending);
  view.tiles.classList.toggle("locked", locked);
  const now = performance.now();
  view.tiles.classList.toggle("cooling", !locked && isCooling(model.cooldowns, "tile", now));

  const playing = isPlaying(state);
  view.toggle.classList.toggle("is-playing", playing);
  view.toggle.setAttribute("aria-label", t(playing ? "kids.pause" : "kids.play"));
  const playback = state ? state.playback : null;
  view.toggle.disabled =
    !playback || (!playback.can_toggle && !playing) || isCooling(model.cooldowns, "toggle", now);
  const skipping = isCooling(model.cooldowns, "skip", now);
  view.prev.disabled = !playback || !playback.can_prev || skipping;
  view.next.disabled = !playback || !playback.can_next || skipping;
  const bedtime = renderBedtime(state);
  renderGames(state);
  view.tiles.hidden = bedtime;
  view.empty.hidden = bedtime || model.tiles.length > 0;
  renderVolume(state ? state.volume : null, bedtime);
  renderLights(state);
  renderPlaying(state, bedtime);
  renderNow(state);
  renderPager();
  followPlaying(state);
  renderOverlay();
  renderError(state);
}

function renderVolume(volume, bedtime = isBedtime(model.state)) {
  if (profile() === "small") {
    renderDots(volume);
  } else {
    renderSlider(volume);
  }
  if (volume) {
    view.volume.setAttribute("aria-valuemax", String(volume.max));
    view.volume.setAttribute("aria-valuenow", String(volume.value ?? 0));
  }
  const known = Boolean(volume && volume.value !== null);
  view.louder.disabled = !known || bedtime || volume.value >= (volume.limit ?? volume.max);
  view.quieter.disabled = !known || volume.value <= 0;
}

// Five dots that grow: lit up to the volume, dimmed above the limit.
function renderDots(volume) {
  if (view.volume.children.length !== DOTS || view.volume.dataset.kind !== "dots") {
    view.volume.dataset.kind = "dots";
    view.volume.replaceChildren(
      ...Array.from({ length: DOTS }, (_, i) => {
        const dot = document.createElement("i");
        dot.style.setProperty("--size", `${18 + 6 * i}px`);
        return dot;
      }),
    );
  }
  const lit = volume ? volumeSegments(volume.value, volume.max, DOTS) : 0;
  const allowed = volume ? limitSegments(volume.limit, volume.max, DOTS) : DOTS;
  [...view.volume.children].forEach((dot, index) => {
    dot.classList.toggle("on", index < lit);
    dot.classList.toggle("over", index >= allowed);
  });
}

// A wide bar; the part above the limit (while fading) is hatched.
function renderSlider(volume) {
  if (view.volume.dataset.kind !== "slider") {
    view.volume.dataset.kind = "slider";
    const fill = document.createElement("i");
    fill.className = "fill";
    const over = document.createElement("i");
    over.className = "over-limit";
    view.volume.replaceChildren(fill, over);
  }
  const [fill, over] = view.volume.children;
  const max = volume && volume.max ? volume.max : 1;
  const value = volume && volume.value ? volume.value : 0;
  const limit = volume && volume.limit !== null && volume.limit !== undefined ? volume.limit : max;
  fill.style.width = `${Math.min(100, (value / max) * 100)}%`;
  over.style.left = `${Math.min(100, (limit / max) * 100)}%`;
}

// The light buttons: one per Hue scene the parents chose. Allowed at any
// time, also at bedtime. They react at the first touch (nothing to swipe here).
function renderLights(state) {
  const lights = state && state.lights;
  const slots = lights ? lights.slots : [];
  view.lights.hidden = slots.length === 0;
  if (view.lights.children.length !== slots.length || view.lights.dataset.key !== slotsKey(slots)) {
    view.lights.dataset.key = slotsKey(slots);
    view.lights.replaceChildren(
      ...slots.map((slot) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "light";
        button.dataset.slot = String(slot.slot);
        button.setAttribute("aria-label", t("kids.light"));
        const img = document.createElement("img");
        img.src = `${PICTURES}/${slot.picture}.svg`;
        img.alt = "";
        button.append(img);
        button.addEventListener("pointerdown", (event) => {
          if (event.isPrimary && (event.pointerType !== "mouse" || event.button === 0)) {
            unlockFeedback();
            toggleLight(slot.slot);
          }
        });
        // A click without a pointer press (detail 0) comes from the keyboard.
        button.addEventListener("click", (event) => {
          if (event.detail === 0) toggleLight(slot.slot);
        });
        return button;
      }),
    );
  }
  const cooling = isCooling(model.cooldowns, "light", performance.now());
  [...view.lights.children].forEach((button, index) => {
    button.classList.toggle("active", Boolean(slots[index] && slots[index].active));
    // Waiting after a tap: the lit scene shows at once, the others fade a little.
    button.classList.toggle("waiting", cooling);
    button.disabled = !lights.available;
  });
}

function slotsKey(slots) {
  return slots.map((slot) => `${slot.slot}:${slot.picture}`).join(",");
}

function toggleLight(slot) {
  if (isCooling(model.cooldowns, "light", performance.now())) return;
  diagNote("light", { target: `light:${slot}` });
  plop();
  hop(view.lights.querySelector(`[data-slot="${slot}"]`));
  const seconds = (model.state && model.state.lights && model.state.lights.cooldown) || 3;
  startCooldown("light", seconds);
  command(() => post(`/api/lights/${slot}/toggle`), "light").then((data) => {
    if (data && data.lights && model.state) {
      model.state.lights = data.lights;
      renderState();
    }
  });
}

// "Klein": the tile that plays, big, like a picture book page; a tap on it
// pauses or continues, the house goes back to all tiles. Shown at once on the
// tap (the tile still starting), gone when nothing of ours is loaded.
function renderPlaying(state, bedtime) {
  const wanted = Boolean(state && state.view && state.view.now_view !== false);
  const loaded = state && state.playback ? state.playback.tile_id : null;
  const id = model.localPending || loaded;
  const tile = model.tiles.find((item) => item.id === id);
  const show = profile() === "small" && wanted && Boolean(tile) && !model.browsing && !bedtime;
  view.playing.hidden = !show;
  view.pager.classList.toggle("now-playing", show);
  if (!show) return;
  const playing = isPlaying(state) || Boolean(model.localPending);
  view.playing.classList.toggle("paused", !playing);
  view.playingArt.setAttribute("aria-label", t(playing ? "kids.pause" : "kids.play"));
  view.playingArt.style.background = placeholderColour(tile.id);
  view.playingPicture.src = `${PICTURES}/${placeholderPicture(tile.id)}.svg`;
  const cover = tile.cover || "";
  if (view.playingCover.getAttribute("src") !== cover) {
    view.playingCover.hidden = true;
    if (cover) view.playingCover.src = cover;
    else view.playingCover.removeAttribute("src");
  }
}

function onPlayingPress(event) {
  if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return;
  unlockFeedback();
  pressPlaying();
}

function pressPlaying() {
  if (model.localPending || isCooling(model.cooldowns, "toggle", performance.now())) return;
  diagNote("playing", { target: "control:playing-art" });
  plop();
  hop(view.playingArt);
  togglePlayback();
}

// "Groß": what is playing, its chapter and how far it got.
function renderNow(state) {
  if (profile() !== "big") return;
  const id = state && state.playback ? state.playback.tile_id : null;
  const tile = model.tiles.find((item) => item.id === id);
  const playing = isPlaying(state);
  const { now } = view;
  if (!tile) {
    now.label.textContent = "";
    now.title.textContent = t("kids.now_idle");
    now.sub.textContent = "";
    now.cover.hidden = true;
    now.picture.hidden = false;
    now.picture.src = `${PICTURES}/moon.svg`;
    now.art.style.background = "";
    now.progress.hidden = true;
    return;
  }
  now.label.textContent = t(playing ? "kids.now_playing" : "kids.now_paused");
  now.title.textContent = tile.title;
  now.art.style.background = placeholderColour(tile.id);
  const coverUrl = tile.cover || "";
  if (coverUrl && now.cover.getAttribute("src") !== coverUrl) now.cover.src = coverUrl;
  now.cover.hidden = !coverUrl;
  now.picture.hidden = Boolean(coverUrl);
  now.picture.src = `${PICTURES}/${placeholderPicture(tile.id)}.svg`;
  const track = state.track;
  if (track && (!model.track || track.at !== model.track.at || track.seconds !== model.track.seconds)) {
    model.track = track;
    model.trackReceived = performance.now();
  }
  if (!track) model.track = null;
  const parts = [];
  if (track && track.count > 1) parts.push(t("kids.track_of", { number: track.number, count: track.count }));
  if (track && track.title && track.title !== tile.title) parts.push(track.title);
  now.sub.textContent = parts.join(" · ");
  renderProgress();
}

function renderProgress() {
  const track = model.track;
  const { now } = view;
  if (!track || !track.duration) {
    now.progress.hidden = true;
    return;
  }
  const seconds = trackSeconds(track, model.trackReceived, performance.now());
  now.progress.hidden = false;
  now.bar.style.width = `${Math.min(100, (seconds / track.duration) * 100)}%`;
  now.position.textContent = formatTime(seconds);
  now.duration.textContent = formatTime(track.duration);
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
  if (state.assets && PAGE_ASSETS && state.assets !== PAGE_ASSETS) {
    // Muckebox was updated: load the new page (only after a successful answer).
    if (!padIsOpen() && !gameIsOpen()) {
      window.location.reload();
      return;
    }
    // Not while a parent types the PIN or a game runs: forget the ETag, so the
    // next poll gets the full state again and reloads once they are closed.
    model.etag = null;
  }
  model.state = state;
  setTouchDiag(Boolean(state.diag));
  setTapSound(!state.view || state.view.tap_sound !== false);
  if (!state.pending) model.localPending = null;
  renderState();
}

function pollSoon() {
  clearTimeout(model.pollTimer);
  model.pollTimer = setTimeout(poll, 150);
}

// The same kind of tap waits a moment ("anti disco"); the server enforces it,
// the tablet only shows it.
function startCooldown(group, seconds) {
  if (!seconds) return;
  model.cooldowns[group] = Math.max(model.cooldowns[group] || 0, performance.now() + seconds * 1000);
  setTimeout(renderState, seconds * 1000 + 50);
  renderState();
}

function cooldownSeconds(group) {
  const cooldown = model.state && model.state.cooldown;
  return cooldown ? cooldown[group] : 0;
}

async function command(action, group = null) {
  try {
    const { data } = await action();
    return data;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      if (error.code === "cooling_down" && group) startCooldown(group, error.retryIn || 1);
      return null; // busy or waiting: ignore
    }
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
  if (!tileTappable(id, state, model.cooldowns, performance.now())) return;
  // The loaded tile again resumes it, like the play button.
  const group = state && state.playback && state.playback.tile_id === id ? "toggle" : "tile";
  model.localPending = id;
  model.shownPlaying = id; // tapped here: the page stays where it is
  model.browsing = false; // a new tile: show it big ("small")
  renderState();
  command(() => post(`/api/tiles/${encodeURIComponent(id)}/play`), group).then((data) => {
    if (!data || data.result !== "accepted") model.localPending = null;
    if (data && data.result === "accepted") startCooldown("tile", cooldownSeconds("tile"));
    if (data && data.result === "resumed") startCooldown("toggle", cooldownSeconds("toggle"));
    renderState();
  });
}

function transport(action) {
  const group = cooldownGroup(action);
  if (isCooling(model.cooldowns, group, performance.now())) return;
  command(() => post(`/api/transport/${action}`), group).then((data) => {
    if (data) startCooldown(group, cooldownSeconds(group));
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
  view.playingArt.addEventListener("pointerdown", onPlayingPress);
  view.playingArt.addEventListener("click", (event) => {
    if (event.detail === 0) pressPlaying(); // the keyboard
  });
  view.playingCover.addEventListener("load", () => {
    view.playingCover.hidden = false;
  });
  view.playingCover.addEventListener("error", () => {
    view.playingCover.hidden = true;
  });
  view.home.addEventListener("click", () => {
    model.browsing = true;
    renderState();
  });
  view.pagePrev.addEventListener("click", () => scrollToPage(model.page - 1));
  view.pageNext.addEventListener("click", () => scrollToPage(model.page + 1));
  view.tiles.addEventListener("scroll", onScroll, { passive: true });
  view.tiles.addEventListener("pointerdown", onPressStart);
  view.tiles.addEventListener("pointermove", onPressMove);
  view.tiles.addEventListener("pointerup", onPressEnd);
  // The browser took over (scrolling the "big" grid): no tap.
  view.tiles.addEventListener("pointercancel", cancelPress);
  view.tiles.addEventListener("lostpointercapture", endPress);
  window.addEventListener("resize", () => scrollToPage(model.page, false));
  // The progress of the "big" layout moves on between two polls.
  setInterval(() => {
    if (profile() === "big" && model.track && model.track.playing) renderProgress();
  }, 1000);
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
