// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Parents' page: log in with the PIN; an overview of today, and one page each
// for the tiles, adding music, usage times, the sleep timer, volume and
// controls, games, setup and the PIN.

import { ApiError, get, post, request } from "./api.js";
import { loadMessages, t, translatePage } from "./i18n.js";
import { placeholderColour, placeholderPicture, volumeSegments } from "./logic.js";

const $ = (id) => document.getElementById(id);
const FLASH_MS = 5000;
const STATUS_MS = 15000; // the overview refreshes itself while it is shown
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const SETUP_TIMEOUT_MS = 30000; // the server gives a room search or test 20 s
const PAGES = ["overview", "tiles", "add", "schedule", "sleep", "volume", "games", "setup", "pin"];
const PICTURES = "/static/pictures";
const SLEEP_CHOICES = [10, 15, 20, 30, 45, 60];
const PREVIEW_DOTS = 5;
let rev = null;
let flashTimer = null;
let statusTimer = null;
let settings = null;
let lastStatus = null;

// -- helpers ----------------------------------------------------------------------

function flash(text, isError = false) {
  const box = $("flash");
  $("flash-text").textContent = text;
  box.classList.toggle("error", isError);
  box.hidden = false;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    box.hidden = true;
  }, FLASH_MS);
}

function errorText(error) {
  if (error instanceof ApiError) {
    const key = `error.${error.code}`;
    const text = t(key, { retry_in: error.retryIn ?? "" });
    return text === key ? t("error.internal_error") : text; // never show a raw key
  }
  return t("error.internal_error");
}

// While a change is on its way, the tile list cannot be touched: a second
// tap would otherwise send an outdated revision.
async function mutate(action, options) {
  const list = $("tiles");
  list.inert = true;
  list.setAttribute("aria-busy", "true");
  try {
    return await guarded(action, options);
  } finally {
    list.inert = false;
    list.removeAttribute("aria-busy");
  }
}

async function guarded(action, { success } = {}) {
  try {
    const result = await action();
    if (success) flash(success);
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showLogin();
    } else if (error instanceof ApiError && (error.code === "rev_conflict" || error.status === 0)) {
      // After a conflict or a timeout the server may have changed anyway.
      await loadTiles();
    }
    flash(errorText(error), true);
    return null;
  }
}

function busy(button, promise) {
  button.disabled = true;
  return promise.finally(() => {
    button.disabled = false;
  });
}

function element(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

// A tile's picture: its cover, or an animal on a colour (as on the tablet).
function fillArt(box, id, cover) {
  box.style.background = placeholderColour(id);
  const picture = box.querySelector(".picture") || box.appendChild(element("img", { className: "picture", alt: "" }));
  picture.src = `${PICTURES}/${placeholderPicture(id)}.svg`;
  const img = box.querySelector(".cover");
  if (!cover) {
    if (img) img.remove();
    return;
  }
  const coverImg = img || box.appendChild(element("img", { className: "cover", alt: "" }));
  coverImg.addEventListener("error", () => coverImg.remove(), { once: true });
  coverImg.src = cover;
}

// − and + next to a number field.
function bindSteppers(root = document) {
  for (const stepper of root.querySelectorAll(".stepper")) {
    const input = stepper.querySelector("input");
    const step = Number(stepper.dataset.step || 1);
    const change = (direction) => {
      const min = Number(input.min);
      const max = Number(input.max);
      const value = Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : min;
      input.value = String(Math.max(min, Math.min(max, value + direction * step)));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    stepper.querySelector(".dec").addEventListener("click", () => change(-1));
    stepper.querySelector(".inc").addEventListener("click", () => change(1));
  }
}

// -- pages ----------------------------------------------------------------------------

function currentPage() {
  const name = window.location.hash.slice(1);
  return PAGES.includes(name) ? name : "overview";
}

function showPage() {
  const page = currentPage();
  for (const section of document.querySelectorAll(".page")) section.hidden = section.dataset.page !== page;
  for (const link of $("nav").querySelectorAll("a")) {
    const current = link.dataset.page === page;
    link.classList.toggle("current", current);
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (page === "overview" && !$("app").hidden) loadStatus();
}

function showOnly(id) {
  for (const section of ["login", "app"]) $(section).hidden = section !== id;
  $("logout").hidden = id !== "app";
  clearInterval(statusTimer);
  if (id === "app") {
    statusTimer = setInterval(() => {
      if (currentPage() === "overview" && document.visibilityState === "visible") loadStatus();
    }, STATUS_MS);
  }
}

function showLogin() {
  showOnly("login");
  $("room-pill").hidden = true;
  $("pin").focus();
}

// -- overview -------------------------------------------------------------------------

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
let serverZone = null;

function greeting(hour) {
  if (hour < 11) return t("admin.greeting_morning");
  if (hour < 18) return t("admin.greeting_day");
  return t("admin.greeting_evening");
}

function renderStatus(status) {
  lastStatus = status;
  serverZone = status.time.zone;
  const now = status.time.now; // local ISO time of the Muckebox
  const hour = Number(now.slice(11, 13));
  const sonos = status.sonos || {};
  const own = ["ok", "starting", "not_configured"].includes(sonos.status);
  const connection = t(own ? `status.${sonos.status}` : `error.${sonos.status}`);

  // The banner and the setup page already point out these two.
  const shownElsewhere = ["not_configured", "pin_generated"];
  const problems = status.config_problems
    .filter((code) => !shownElsewhere.includes(code))
    .map((code) => t(`error.${code}`));
  if (status.library_problem) {
    problems.push(t(`error.${status.library_problem.code}`, status.library_problem));
  }
  if (!own) problems.unshift(connection);
  const fine = problems.length === 0 && sonos.status === "ok";
  $("greeting").textContent = `${greeting(hour)} ${t(fine ? "admin.all_good" : "admin.needs_attention")}`;

  $("room-pill").hidden = !sonos.room;
  $("room-name").textContent = sonos.room || "";
  $("room-dot").className = `dot ${sonos.status === "ok" ? "ok" : own ? "" : "bad"}`;

  $("today-clock").textContent = now.slice(11, 16);
  renderScheduleStatus(status.schedule);
  renderTodayBar(status.schedule, now);
  renderSleepStatus(status.sleep_timer);

  $("stat-volume").textContent = String(status.volume_guard.max);
  $("stat-corrections").textContent = t("admin.corrections_count", { count: status.volume_guard.corrections });
  const used = Math.round(status.games.used_today / 60);
  const limit = Math.round(status.games.daily_seconds / 60);
  $("stat-games").textContent = String(used);
  $("stat-games-limit").textContent = t("admin.games_limit", { limit });
  $("stat-games-bar").style.width = `${limit ? Math.min(100, (used / limit) * 100) : 0}%`;
  $("games-status").textContent = t("admin.games_status", { used, limit });
  $("stat-sleep").textContent = String(status.sleep_timer.minutes);
  $("stat-sleep-note").textContent = status.sleep_timer.ends_at
    ? t("admin.sleep_running", { time: clockText(status.sleep_timer.ends_at) })
    : t(status.sleep_timer.enabled ? "admin.sleep_visible" : "admin.sleep_hidden");

  const rows = [
    ["admin.room", sonos.room || "–"],
    ["admin.connection", connection],
    ["admin.corrections", String(status.volume_guard.corrections)],
    ["admin.clock", `${now.slice(11, 16)} (${status.time.zone})`],
  ];
  if (status.idle.paused_at) {
    const at = status.idle.paused_at;
    rows.push(["admin.idle_paused", `${at.slice(8, 10)}.${at.slice(5, 7)}. ${at.slice(11, 16)}`]);
  }
  $("status").replaceChildren(
    ...rows.flatMap(([key, value]) => [element("dt", { textContent: t(key) }), element("dd", { textContent: value })]),
  );
  $("problems").replaceChildren(...problems.map((text) => element("li", { textContent: text })));
  renderZone(status.time.zone, now);
  $("version").textContent = `${t("admin.version")} ${status.version}`;
  $("source").href = status.source_url;
}

// Today from 0 to 24 h: the allowed window (or the override) and a "now" line.
function renderTodayBar(schedule, nowIso) {
  const bar = $("today-bar");
  const off = schedule.phase === "off" || !settings;
  bar.hidden = off;
  if (off) return;
  const [year, month, day] = nowIso.slice(0, 10).split("-").map(Number);
  const weekday = DAYS[(new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7];
  const window = settings.schedule.days[weekday];
  const minutes = (text) => {
    const [h, m] = text.split(":").map(Number);
    return h * 60 + m;
  };
  let from = window ? minutes(window.from) : 0;
  let to = window ? minutes(window.to) : 1440;
  if (schedule.override_until && sameDay(schedule.override_until, nowIso)) {
    to = Math.max(to, minutes(localTime(schedule.override_until)));
  } else if (schedule.override_until) {
    to = 1440;
  }
  if (from > to) from = to;
  const fill = bar.querySelector(".window");
  fill.style.left = `${(from / 1440) * 100}%`;
  fill.style.width = `${((to - from) / 1440) * 100}%`;
  bar.querySelector(".now").style.left = `${(minutes(nowIso.slice(11, 16)) / 1440) * 100}%`;
}

function renderZone(server, nowIso) {
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const differs = Boolean(browser) && browser !== server;
  $("zone-ok").hidden = differs;
  $("zone-adopt").hidden = !differs;
  $("zone-text").textContent = differs
    ? t("admin.zone_differs", { server, browser })
    : t("admin.zone_same", { zone: server, time: nowIso.slice(11, 16) });
  if (!differs) return;
  $("zone-adopt").textContent = t("admin.zone_adopt", { browser });
  $("zone-adopt").onclick = async (event) => {
    const result = await busy(
      event.target,
      guarded(() => request("PUT", "/api/admin/settings/time-zone", { body: { zone: browser } }), {
        success: t("admin.saved"),
      }),
    );
    if (result) loadStatus();
  };
}

// Times are shown in the Muckebox's zone: that is the one the usage times use.
function formatIn(epoch, options) {
  try {
    return new Intl.DateTimeFormat("de-DE", { ...options, timeZone: serverZone || undefined }).format(
      new Date(epoch * 1000),
    );
  } catch {
    return new Intl.DateTimeFormat("de-DE", options).format(new Date(epoch * 1000)); // unknown zone name
  }
}

function localTime(epoch) {
  return formatIn(epoch, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

function sameDay(epoch, nowIso) {
  const date = formatIn(epoch, { year: "numeric", month: "2-digit", day: "2-digit" }); // dd.mm.yyyy
  const [day, month, year] = date.split(".");
  return `${year}-${month}-${day}` === nowIso.slice(0, 10);
}

// "19:00" today, "Fr., 07:00" on another day.
function clockText(epoch) {
  if (lastStatus && sameDay(epoch, lastStatus.time.now)) return localTime(epoch);
  return formatIn(epoch, { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

function renderScheduleStatus(schedule) {
  const texts = {
    off: () => t("admin.phase_off"),
    open: () => (schedule.ends_at ? t("admin.phase_open", { time: clockText(schedule.ends_at) }) : t("admin.phase_open_free")),
    fading: () => t("admin.phase_fading", { time: clockText(schedule.ends_at) }),
    closed: () =>
      schedule.opens_at ? t("admin.phase_closed", { time: clockText(schedule.opens_at) }) : t("admin.phase_closed_open_end"),
  };
  let text = (texts[schedule.phase] || texts.off)();
  if (schedule.override_until) text += ` ${t("admin.override_until", { time: clockText(schedule.override_until) })}`;
  $("schedule-status").textContent = text;
  $("today-note").textContent = schedule.phase === "off" ? "" : t("admin.today_note");
  $("override-buttons").hidden = schedule.phase === "off"; // nothing to release
  // While an ended override fades out there is nothing left to end.
  $("override-end").hidden = !schedule.override_until || schedule.phase === "fading";
}

async function override(value, button) {
  const body = value === "morning" ? { until: "morning" } : { minutes: Number(value) };
  const result = await busy(button, guarded(() => post("/api/admin/override", body)));
  if (result) {
    renderScheduleStatus(result.data.schedule);
    loadStatus();
  }
}

// -- tiles --------------------------------------------------------------------------------

let knownTiles = [];

function renderTiles(data) {
  rev = data.rev;
  knownTiles = data.tiles;
  if (settings) renderDanceOptions();
  $("nav-tiles").textContent = String(data.tiles.length);
  $("tiles-empty").hidden = data.tiles.length > 0;
  $("preview-empty").hidden = data.tiles.length > 0;
  $("preview").replaceChildren(
    ...data.tiles.map((tile) => {
      const art = element("span", { className: "art" });
      fillArt(art, tile.id, tile.cover);
      return element("span", { className: "preview-tile" }, art, element("span", { textContent: tile.title }));
    }),
  );
  const list = $("tiles");
  const template = $("tile-row");
  list.replaceChildren();
  data.tiles.forEach((tile, index) => {
    const row = template.content.firstElementChild.cloneNode(true);
    translatePage(row);
    row.querySelector(".number").textContent = String(index + 1);
    fillArt(row.querySelector(".thumb"), tile.id, tile.cover);
    const input = row.querySelector("input[name=title]");
    input.value = tile.title;
    const source = tile.source;
    row.querySelector(".meta").textContent =
      source.type === "favorite" ? source.description : `${source.service} · ${source.kind}`;
    // Renamed in place: saved on Enter or when the field is left.
    const rename = (event) => {
      if (event) event.preventDefault();
      if (input.value.trim() === tile.title || !input.value.trim()) return;
      mutate(() => request("PATCH", `/api/admin/tiles/${tile.id}`, { body: { title: input.value, rev } }), {
        success: t("admin.saved"),
      }).then((result) => result && renderTiles(result.data));
    };
    row.querySelector(".rename").addEventListener("submit", rename);
    input.addEventListener("change", () => rename());
    renderResume(row, tile);
    const up = row.querySelector(".up");
    const down = row.querySelector(".down");
    up.disabled = index === 0;
    down.disabled = index === data.tiles.length - 1;
    for (const [button, direction] of [
      [up, "up"],
      [down, "down"],
    ]) {
      button.addEventListener("click", () =>
        mutate(() => post(`/api/admin/tiles/${tile.id}/move`, { direction, rev })).then(
          (result) => result && renderTiles(result.data),
        ),
      );
    }
    row.querySelector("input[type=file]").addEventListener("change", (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > MAX_UPLOAD_BYTES) {
        flash(t("error.upload_too_large"), true);
        return;
      }
      const form = new FormData();
      form.append("cover", file);
      form.append("rev", String(rev));
      mutate(() => request("PUT", `/api/admin/tiles/${tile.id}/cover`, { body: form, timeout: 60000 }), {
        success: t("admin.saved"),
      }).then((result) => result && renderTiles(result.data));
    });
    row.querySelector(".delete").addEventListener("click", () => {
      if (!window.confirm(t("admin.delete_confirm", { title: tile.title }))) return;
      mutate(() => request("DELETE", `/api/admin/tiles/${tile.id}?rev=${rev}`)).then((result) => {
        if (result) {
          renderTiles(result.data);
          loadFavorites();
        }
      });
    });
    list.append(row);
  });
}

// "Weiterhören": albums continue where they stopped.
function renderResume(row, tile) {
  const box = row.querySelector(".resume");
  const resume = tile.resume;
  box.hidden = !resume.available;
  if (!resume.available) return;
  const toggle = row.querySelector(".resume-toggle");
  toggle.checked = resume.enabled;
  toggle.addEventListener("change", () =>
    mutate(() =>
      request("PUT", `/api/admin/tiles/${tile.id}/resume`, {
        // Back to "automatic" when it matches the default again.
        body: { enabled: toggle.checked === resume.default ? null : toggle.checked, rev },
      }),
    ).then((result) => result && renderTiles(result.data)),
  );
  const position = resume.position;
  const text = row.querySelector(".resume-position");
  const restart = row.querySelector(".resume-restart");
  const shown = resume.enabled && position;
  restart.hidden = !shown;
  text.textContent = "";
  if (shown) {
    const minutes = Math.floor(position.seconds / 60);
    const seconds = String(position.seconds % 60).padStart(2, "0");
    text.textContent = t("admin.resume_position", { track: position.track, time: `${minutes}:${seconds}` });
  }
  restart.addEventListener("click", () =>
    mutate(() => request("DELETE", `/api/admin/tiles/${tile.id}/position`), { success: t("admin.saved") }).then(
      (result) => result && renderTiles(result.data),
    ),
  );
}

// -- adding music ---------------------------------------------------------------------------

function renderFavorites(favorites) {
  const list = $("favorites");
  const template = $("favorite-row");
  list.replaceChildren();
  for (const favorite of favorites) {
    const row = template.content.firstElementChild.cloneNode(true);
    setThumb(
      row.querySelector(".thumb"),
      favorite.has_art ? `/api/admin/favorite-art?item_id=${encodeURIComponent(favorite.item_id)}` : null,
      favorite.item_id,
    );
    row.querySelector("strong").textContent = favorite.title;
    const small = row.querySelector("small");
    const button = row.querySelector(".add");
    if (!favorite.playable) {
      row.classList.add("unplayable");
      small.textContent = t(`reason.${favorite.reason}`);
      button.remove();
    } else {
      small.textContent = favorite.description;
      button.textContent = t(favorite.tile_id ? "admin.added" : "admin.add");
      button.disabled = Boolean(favorite.tile_id);
      button.addEventListener("click", () =>
        busy(
          button,
          mutate(() =>
            post("/api/admin/tiles", { source: "favorite", item_id: favorite.item_id }, { timeout: 30000 }),
          ).then((result) => {
            if (result) {
              showWarnings(result.data.warnings);
              renderTiles(result.data);
            }
            loadFavorites(); // also after a timeout: the tile may exist anyway
          }),
        ),
      );
    }
    list.append(row);
  }
}

// The favorite's picture, or an animal on a colour if it has none.
function setThumb(img, src, key) {
  const placeholder = () => {
    const box = element("span", { className: "thumb" });
    fillArt(box, key, null);
    img.replaceWith(box);
  };
  if (!src) {
    placeholder();
    return;
  }
  img.addEventListener("error", placeholder, { once: true });
  img.src = src;
}

function showWarnings(warnings) {
  const texts = (warnings || []).map((code) => t(`warning.${code}`));
  flash(texts.length ? texts.join(" ") : t("admin.saved"));
}

// -- settings --------------------------------------------------------------------------------

function renderSettings(data) {
  settings = data.settings;
  $("pin-banner").hidden = !settings.pin_generated;
  $("nav-pin").classList.toggle("warn", settings.pin_generated);
  $("room-current").textContent = settings.room
    ? t("admin.room_current", { room: settings.room })
    : t("admin.room_none");
  if (document.activeElement !== $("seed-ip")) $("seed-ip").value = settings.seed_ip || "";
  $("favorites-no-room").hidden = Boolean(settings.room);
  $("refresh").disabled = !settings.room;
}

// The volume page: limit, step, waits and the kids' layout (one form, two saves).
function renderVolumeForm(current) {
  $("max-volume").value = String(current.max_volume);
  $("volume-step").value = String(current.volume_step);
  $("tap-cooldown").value = String(current.controls.tap_cooldown);
  $("idle-minutes").value = String(current.controls.idle_minutes);
  for (const radio of document.querySelectorAll("input[name=profile]")) {
    radio.checked = radio.value === current.controls.profile;
  }
  $("skip-buttons").checked = current.controls.skip_buttons;
  renderVolumePreview();
}

function renderVolumePreview() {
  const max = $("max-volume").valueAsNumber || 1;
  const step = $("volume-step").valueAsNumber || 1;
  $("max-volume-value").textContent = String(max);
  // How the tablet looks at the limit: the dots of the "small" layout, all lit
  // at the parents' maximum (the bar's scale is the limit itself).
  const lit = volumeSegments(max, max, PREVIEW_DOTS);
  $("preview-dots").replaceChildren(
    ...Array.from({ length: PREVIEW_DOTS }, (_, i) => {
      const dot = element("i", { className: i < lit ? "on" : "" });
      dot.style.width = dot.style.height = `${18 + 6 * i}px`;
      return dot;
    }),
  );
  $("preview-taps").textContent = t("admin.taps_to_limit", { taps: Math.ceil(max / step) });
  const small = document.querySelector("input[name=profile][value=small]");
  $("skip-buttons").closest("label").hidden = !small.checked;
}

async function saveVolume(event) {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  const volume = { max_volume: $("max-volume").valueAsNumber, volume_step: $("volume-step").valueAsNumber };
  const profile = document.querySelector("input[name=profile]:checked");
  const controls = {
    tap_cooldown: $("tap-cooldown").valueAsNumber,
    idle_minutes: $("idle-minutes").valueAsNumber,
    profile: profile ? profile.value : "small",
    skip_buttons: $("skip-buttons").checked,
  };
  const result = await busy(
    button,
    guarded(async () => {
      await request("PUT", "/api/admin/settings/volume", { body: volume });
      return request("PUT", "/api/admin/settings/controls", { body: controls });
    }, { success: t("admin.saved") }),
  );
  if (!result) return;
  renderSettings(result.data);
  renderVolumeForm(result.data.settings);
  loadStatus();
}

// -- rooms ---------------------------------------------------------------------------------------

function showRoomsMessage(text) {
  $("rooms").replaceChildren(element("li", { className: "hint", textContent: text }));
}

// `seed` is the speaker address the list was found with; choosing a room saves it.
function renderRooms(rooms, seed) {
  if (!rooms.length) {
    showRoomsMessage(t("admin.rooms_none"));
    return;
  }
  const list = $("rooms");
  const template = $("room-row");
  list.replaceChildren();
  for (const room of rooms) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("strong").textContent = room.name;
    row.querySelector("small").textContent = room.grouped ? `${room.ip} · ${t("admin.room_grouped")}` : room.ip;
    const button = row.querySelector(".choose");
    const sameRoom = Boolean(settings) && room.name === settings.room;
    // The same room found with another speaker address can be saved again.
    const current = sameRoom && seed === (settings.seed_ip || null);
    row.classList.toggle("current", sameRoom);
    button.textContent = t(current ? "admin.chosen" : sameRoom ? "admin.apply" : "admin.choose");
    button.disabled = current;
    button.addEventListener("click", () => chooseRoom(room.name, rooms, seed, button));
    list.append(row);
  }
}

async function chooseRoom(name, rooms, seed, button) {
  const result = await busy(
    button,
    guarded(
      () =>
        request("PUT", "/api/admin/settings/room", {
          body: { room: name, seed_ip: seed },
          timeout: SETUP_TIMEOUT_MS,
        }),
      { success: t("admin.room_saved", { room: name }) },
    ),
  );
  if (!result) return;
  renderSettings(result.data);
  renderRooms(rooms, seed);
  loadStatus();
  loadFavorites();
}

async function searchRooms(refresh) {
  const button = $("room-search").querySelector("button");
  if (button.disabled) return; // one search at a time, including the automatic one
  const seed = $("seed-ip").value.trim() || null;
  button.disabled = true;
  showRoomsMessage(t("admin.searching"));
  try {
    const result = await post("/api/admin/rooms/search", { seed_ip: seed, refresh }, { timeout: SETUP_TIMEOUT_MS });
    renderRooms(result.data.rooms, seed);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showLogin();
      return;
    }
    // Stays visible (unlike a flash): it tells the parents what to do next.
    showRoomsMessage(error.code === "room_not_found" ? t("admin.rooms_none") : errorText(error));
  } finally {
    button.disabled = false;
  }
}

// -- games ------------------------------------------------------------------------------------------

const GAMES = ["freeze_dance", "sound_quiz", "move_like", "breathing"];
const GAME_COLOURS = { freeze_dance: "#ffe5a8", sound_quiz: "#cfe8e9", move_like: "#f8d3c5", breathing: "#d5dceb" };
let danceTile = null;

function buildGameRows() {
  const box = $("game-rows");
  const template = $("game-row");
  for (const game of GAMES) {
    const card = template.content.firstElementChild.cloneNode(true);
    translatePage(card);
    card.dataset.game = game;
    card.querySelector(".game-picture").style.background = GAME_COLOURS[game];
    card.querySelector(".game-picture img").src = `${PICTURES}/${game}.svg`;
    card.querySelector(".game-name").textContent = t(`game.${game}`);
    card.querySelector(".game-tag").textContent = t(`admin.game_tag_${game}`);
    card.querySelector(".game-about").textContent = t(`admin.game_about_${game}`);
    card.querySelector(".game-enabled").setAttribute("aria-label", t(`game.${game}`));
    for (const radio of card.querySelectorAll(".game-level")) radio.name = `level-${game}`;
    // The breathing exercise has one calm level for everybody.
    card.querySelector(".levels").hidden = game === "breathing";
    box.append(card);
  }
}

function renderGamesForm(games) {
  $("games-minutes").value = String(games.daily_minutes);
  $("nav-games").textContent = String(GAMES.filter((game) => games.items[game].enabled).length);
  for (const card of $("game-rows").children) {
    const item = games.items[card.dataset.game];
    card.querySelector(".game-enabled").checked = item.enabled;
    for (const radio of card.querySelectorAll(".game-level")) radio.checked = Number(radio.value) === item.level;
  }
  danceTile = games.dance_tile;
  renderDanceOptions(false);
}

function renderDanceOptions(keepChoice = true) {
  const select = $("dance-tile");
  // Keep an unsaved choice when only the tile list changed.
  const chosen = keepChoice && select.options.length ? select.value : danceTile;
  const current = element("option", { value: "", textContent: t("admin.dance_current") });
  select.replaceChildren(
    current,
    ...knownTiles.map((tile) => element("option", { value: tile.id, textContent: tile.title })),
  );
  select.value = knownTiles.some((tile) => tile.id === chosen) ? chosen : "";
}

function gamesFromForm() {
  const items = {};
  for (const card of $("game-rows").children) {
    const level = card.querySelector(".game-level:checked");
    items[card.dataset.game] = {
      enabled: card.querySelector(".game-enabled").checked,
      level: level ? Number(level.value) : 2,
    };
  }
  return { daily_minutes: $("games-minutes").valueAsNumber, dance_tile: $("dance-tile").value || null, items };
}

async function loadCredits() {
  const result = await guarded(() => get("/api/credits"));
  if (!result) return;
  $("credits-list").replaceChildren(
    ...result.data.credits.map((item) => {
      const own = item.name === "pictures" || item.name.startsWith("font_");
      const name = own ? t(`admin.credit_${item.name}`) : t(`sound.${item.name}`);
      const source = element("a", { href: item.source, textContent: name, rel: "noopener" });
      const licence = element("a", { href: item.license_url, textContent: item.license, rel: "noopener" });
      return element("li", {}, source, ` – ${item.author}, `, licence);
    }),
  );
}

// -- sleep timer --------------------------------------------------------------------------------------

function renderSleepForm(timer) {
  $("sleep-enabled").checked = timer.enabled;
  $("sleep-minutes").value = String(timer.minutes);
  $("sleep-wake").value = timer.wake;
  // The usual durations, plus the saved one if it is another.
  const choices = [...new Set([...SLEEP_CHOICES, timer.minutes])].sort((a, b) => a - b);
  $("sleep-choices").replaceChildren(
    ...choices.map((minutes) => {
      const radio = element("input", { type: "radio", name: "sleep-choice", value: String(minutes) });
      radio.checked = minutes === timer.minutes;
      radio.addEventListener("change", () => {
        $("sleep-minutes").value = radio.value;
      });
      return element("label", {}, radio, t("admin.minutes_short", { minutes }));
    }),
  );
}

function renderSleepStatus(timer) {
  const running = Boolean(timer.ends_at);
  $("sleep-status").hidden = !running;
  $("sleep-cancel").hidden = !running;
  if (running) $("sleep-status-text").textContent = t("admin.sleep_running", { time: clockText(timer.ends_at) });
}

// -- usage times ----------------------------------------------------------------------------------------

function buildDays() {
  const box = $("days");
  const template = $("day-row");
  for (const day of DAYS) {
    const row = template.content.firstElementChild.cloneNode(true);
    translatePage(row);
    row.dataset.day = day;
    row.querySelector(".day").textContent = t(`admin.day_${day}`);
    row.querySelector(".free").addEventListener("change", () => updateDayRow(row));
    for (const input of row.querySelectorAll("input[type=time]")) {
      input.addEventListener("input", () => updateDayRow(row));
    }
    box.append(row);
  }
}

// The bar shows the window (a free day: the whole day, in another colour).
function updateDayRow(row) {
  const free = row.querySelector(".free").checked;
  for (const input of row.querySelectorAll("input[type=time]")) input.disabled = free;
  const minutes = (text, fallback) => {
    if (!text) return fallback;
    const [h, m] = text.split(":").map(Number);
    return h * 60 + m;
  };
  const from = free ? 0 : minutes(row.querySelector(".from").value, 0);
  let to = free ? 1440 : minutes(row.querySelector(".to").value, 1440);
  if (to === 0) to = 1440; // "00:00" as the end: midnight
  const bar = row.querySelector(".day-bar");
  bar.classList.toggle("is-free", free);
  const fill = bar.querySelector(".window");
  fill.style.left = `${(Math.min(from, to) / 1440) * 100}%`;
  fill.style.width = `${(Math.max(0, to - from) / 1440) * 100}%`;
}

// A time input cannot show 24:00: "00:00" as the end means midnight.
function renderScheduleForm(schedule) {
  $("schedule-enabled").checked = schedule.enabled;
  $("fade-minutes").value = String(schedule.fade_minutes);
  // Nothing set up yet: suggest a window on every day instead of "free".
  const fresh = !schedule.enabled && Object.values(schedule.days).every((day) => !day);
  for (const row of $("days").children) {
    const window = schedule.days[row.dataset.day];
    row.querySelector(".free").checked = !window && !fresh;
    row.querySelector(".from").value = window ? window.from : "07:00";
    row.querySelector(".to").value = window ? (window.to === "24:00" ? "00:00" : window.to) : "19:00";
    updateDayRow(row);
  }
}

function scheduleFromForm() {
  const days = {};
  for (const row of $("days").children) {
    const to = row.querySelector(".to").value;
    days[row.dataset.day] = row.querySelector(".free").checked
      ? null
      : { from: row.querySelector(".from").value, to: to === "00:00" ? "24:00" : to };
  }
  return { enabled: $("schedule-enabled").checked, fade_minutes: $("fade-minutes").valueAsNumber, days };
}

// -- loading ----------------------------------------------------------------------------------------------

async function loadStatus() {
  const result = await guarded(() => get("/api/admin/status"));
  if (result) renderStatus(result.data);
}

async function loadTiles() {
  const result = await guarded(() => get("/api/admin/tiles"));
  if (result) renderTiles(result.data);
}

async function loadSettings() {
  const result = await guarded(() => get("/api/admin/settings"));
  if (!result) return;
  renderSettings(result.data);
  // Forms only here and after their own save: never over unsaved edits.
  const current = result.data.settings;
  renderVolumeForm(current);
  renderScheduleForm(current.schedule);
  renderSleepForm(current.sleep_timer);
  renderGamesForm(current.games);
}

async function loadFavorites(refresh = false) {
  if (!settings || !settings.room) {
    $("favorites").replaceChildren(); // without a room there are no favorites to show
    return;
  }
  $("favorites").replaceChildren(element("li", { className: "hint", textContent: t("admin.loading") }));
  const result = await guarded(() => get(`/api/admin/favorites${refresh ? "?refresh=1" : ""}`, { timeout: 20000 }));
  if (result) renderFavorites(result.data.favorites);
  else $("favorites").replaceChildren();
}

async function showApp() {
  showOnly("app");
  $("rooms").replaceChildren();
  await loadSettings();
  const first = settings && !settings.room;
  if (first && currentPage() === "overview") window.location.hash = "setup";
  showPage();
  await Promise.all([loadStatus(), loadTiles(), loadFavorites(), first ? searchRooms(false) : null]);
}

// -- wiring ----------------------------------------------------------------------------------------------

async function saveForm(event, path, body, after) {
  event.preventDefault();
  const result = await busy(
    event.target.querySelector("button[type=submit]"),
    guarded(() => request("PUT", path, { body }), { success: t("admin.saved") }),
  );
  if (result) after(result.data.settings);
}

function bind() {
  window.addEventListener("hashchange", showPage);
  bindSteppers();
  $("login").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await guarded(() => post("/api/admin/login", { pin: $("pin").value }));
    $("pin").value = "";
    if (result) showApp();
  });
  $("logout").addEventListener("click", async () => {
    await guarded(() => post("/api/admin/logout"));
    showLogin();
  });
  $("refresh").addEventListener("click", (event) => busy(event.target, loadFavorites(true)));
  $("room-search").addEventListener("submit", (event) => {
    event.preventDefault();
    searchRooms(true);
  });
  $("volume").addEventListener("submit", saveVolume);
  $("volume").addEventListener("input", renderVolumePreview);
  $("volume").addEventListener("change", renderVolumePreview);
  buildDays();
  $("copy-monday").addEventListener("click", () => {
    const [monday, ...others] = $("days").children;
    for (const row of others) {
      for (const name of ["from", "to", "free"]) {
        const source = monday.querySelector(`.${name}`);
        const target = row.querySelector(`.${name}`);
        if (name === "free") target.checked = source.checked;
        else target.value = source.value;
      }
      updateDayRow(row);
    }
  });
  $("schedule").addEventListener("submit", (event) =>
    saveForm(event, "/api/admin/settings/schedule", scheduleFromForm(), (current) => {
      renderScheduleForm(current.schedule);
      settings.schedule = current.schedule;
      loadStatus();
    }),
  );
  buildGameRows();
  $("credits").addEventListener("toggle", loadCredits, { once: true });
  $("games").addEventListener("submit", (event) =>
    saveForm(event, "/api/admin/settings/games", gamesFromForm(), (current) => {
      renderGamesForm(current.games);
      loadStatus();
    }),
  );
  $("sleep-timer").addEventListener("submit", (event) =>
    saveForm(
      event,
      "/api/admin/settings/sleep-timer",
      {
        enabled: $("sleep-enabled").checked,
        minutes: Number($("sleep-minutes").value),
        wake: $("sleep-wake").value,
      },
      (current) => {
        renderSleepForm(current.sleep_timer);
        loadStatus();
      },
    ),
  );
  $("sleep-cancel").addEventListener("click", async (event) => {
    const result = await busy(event.target, guarded(() => request("DELETE", "/api/admin/sleep-timer")));
    if (result) {
      renderSleepStatus(result.data.sleep_timer);
      loadStatus();
    }
  });
  for (const button of document.querySelectorAll("[data-override]")) {
    button.addEventListener("click", () => override(button.dataset.override, button));
  }
  $("override-end").addEventListener("click", async (event) => {
    const result = await busy(event.target, guarded(() => request("DELETE", "/api/admin/override")));
    if (result) {
      renderScheduleStatus(result.data.schedule);
      loadStatus();
    }
  });
  $("pin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("pin-new").value !== $("pin-repeat").value) {
      flash(t("admin.pin_mismatch"), true);
      return;
    }
    const body = { current: $("pin-current").value, new: $("pin-new").value };
    const result = await busy(
      event.target.querySelector("button[type=submit]"),
      guarded(() => post("/api/admin/pin", body), { success: t("admin.pin_changed") }),
    );
    if (!result) return;
    event.target.reset();
    renderSettings(result.data);
    loadStatus();
  });
  $("link").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    const url = $("link-url").value.trim();
    busy(
      button,
      mutate(() => post("/api/admin/tiles", { source: "sharelink", url }, { timeout: 30000 })).then((result) => {
        if (!result) return;
        $("link-url").value = "";
        showWarnings(result.data.warnings);
        renderTiles(result.data);
      }),
    );
  });
}

async function start() {
  loadMessages();
  translatePage();
  bind();
  showPage();
  const result = await guarded(() => get("/api/admin/session"));
  if (!result) return;
  if (result.data.logged_in) showApp();
  else showLogin();
}

start();
