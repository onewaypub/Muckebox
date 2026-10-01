// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Parents' page: log in with the PIN, choose the room, set the volume limit
// and the PIN, manage tiles, add favorites and links.

import { ApiError, get, post, request } from "./api.js";
import { loadMessages, t, translatePage } from "./i18n.js";

const $ = (id) => document.getElementById(id);
const FLASH_MS = 5000;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const SETUP_TIMEOUT_MS = 30000; // the server gives a room search or test 20 s
let rev = null;
let flashTimer = null;
let settings = null;

// -- helpers ----------------------------------------------------------------------

function flash(text, isError = false) {
  const box = $("flash");
  box.textContent = text;
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

// -- views --------------------------------------------------------------------------

function showOnly(id) {
  for (const section of ["login", "app"]) $(section).hidden = section !== id;
  $("logout").hidden = id !== "app";
}

function showLogin() {
  showOnly("login");
  $("pin").focus();
}

function renderStatus(status) {
  serverZone = status.time.zone;
  renderScheduleStatus(status.schedule);
  renderSleepStatus(status.sleep_timer);
  $("games-status").textContent = t("admin.games_status", {
    used: Math.round(status.games.used_today / 60),
    limit: Math.round(status.games.daily_seconds / 60),
  });
  const list = $("status");
  const sonos = status.sonos || {};
  const own = ["ok", "starting", "not_configured"].includes(sonos.status);
  const connection = t(own ? `status.${sonos.status}` : `error.${sonos.status}`);
  const rows = [
    ["admin.room", sonos.room || "–"],
    ["admin.connection", connection],
    ["admin.max_volume", String(status.volume_guard.max)],
    ["admin.corrections", String(status.volume_guard.corrections)],
    ["admin.clock", `${status.time.now.slice(11, 16)} (${status.time.zone})`],
  ];
  if (status.idle.paused_at) {
    const at = status.idle.paused_at; // local ISO time of the Muckebox
    rows.push(["admin.idle_paused", `${at.slice(8, 10)}.${at.slice(5, 7)}. ${at.slice(11, 16)}`]);
  }
  list.replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const term = document.createElement("dt");
      term.textContent = t(key);
      const detail = document.createElement("dd");
      detail.textContent = value;
      return [term, detail];
    }),
  );
  // The banner and the setup section already point out these two.
  const shownElsewhere = ["not_configured", "pin_generated"];
  const problems = status.config_problems
    .filter((code) => !shownElsewhere.includes(code))
    .map((code) => t(`error.${code}`));
  if (status.library_problem) {
    problems.push(t(`error.${status.library_problem.code}`, status.library_problem));
  }
  $("problems").replaceChildren(
    ...problems.map((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      return item;
    }),
  );
  renderZoneHint(status.time.zone);
  $("version").textContent = `${t("admin.version")} ${status.version}`;
  $("source").href = status.source_url;
}

// Usage times follow the Muckebox's zone; offer this device's zone if it differs.
function renderZoneHint(server) {
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const differs = Boolean(browser) && browser !== server;
  $("zone-hint").hidden = !differs;
  if (!differs) return;
  $("zone-text").textContent = t("admin.zone_differs", { server, browser });
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

function renderTiles(data) {
  rev = data.rev;
  knownTiles = data.tiles;
  if (settings) renderDanceOptions();
  const list = $("tiles");
  const template = $("tile-row");
  list.replaceChildren();
  $("tiles-empty").hidden = data.tiles.length > 0;
  data.tiles.forEach((tile, index) => {
    const row = template.content.firstElementChild.cloneNode(true);
    translatePage(row);
    setThumb(row.querySelector(".thumb"), tile.cover);
    const input = row.querySelector("input[name=title]");
    input.value = tile.title;
    const source = tile.source;
    row.querySelector(".meta").textContent =
      source.type === "favorite" ? source.description : `${source.service} · ${source.kind}`;
    row.querySelector(".rename").addEventListener("submit", (event) => {
      event.preventDefault();
      mutate(() => request("PATCH", `/api/admin/tiles/${tile.id}`, { body: { title: input.value, rev } }), {
        success: t("admin.saved"),
      }).then((result) => result && renderTiles(result.data));
    });
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

function renderSettings(data) {
  settings = data.settings;
  $("pin-banner").hidden = !settings.pin_generated;
  $("room-current").textContent = settings.room
    ? t("admin.room_current", { room: settings.room })
    : t("admin.room_none");
  if (document.activeElement !== $("seed-ip")) $("seed-ip").value = settings.seed_ip || "";
  for (const [id, value] of [
    ["max-volume", settings.max_volume],
    ["volume-step", settings.volume_step],
    ["tap-cooldown", settings.controls.tap_cooldown],
    ["idle-minutes", settings.controls.idle_minutes],
  ]) {
    if (document.activeElement !== $(id)) $(id).value = String(value);
  }
  $("favorites-no-room").hidden = Boolean(settings.room);
  $("refresh").disabled = !settings.room;
}

function showRoomsMessage(text) {
  $("rooms").replaceChildren(Object.assign(document.createElement("li"), { className: "hint", textContent: text }));
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
  text.hidden = restart.hidden = !(resume.enabled && position);
  if (resume.enabled && position) {
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

function renderFavorites(favorites) {
  const list = $("favorites");
  const template = $("favorite-row");
  list.replaceChildren();
  for (const favorite of favorites) {
    const row = template.content.firstElementChild.cloneNode(true);
    setThumb(
      row.querySelector(".thumb"),
      favorite.has_art ? `/api/admin/favorite-art?item_id=${encodeURIComponent(favorite.item_id)}` : null,
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

// A picture, or a neutral placeholder if there is none or it fails to load.
function setThumb(img, src) {
  const placeholder = () => {
    const box = document.createElement("span");
    box.className = "thumb placeholder";
    box.textContent = "♪";
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

// -- loading ------------------------------------------------------------------------

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
  renderScheduleForm(result.data.settings.schedule);
  renderSleepForm(result.data.settings.sleep_timer);
  renderGamesForm(result.data.settings.games);
}

// -- games --------------------------------------------------------------------------

const GAMES = ["freeze_dance", "sound_quiz", "move_like", "breathing"];
let knownTiles = [];
let danceTile = null;

function buildGameRows() {
  const box = $("game-rows");
  const template = $("game-row");
  for (const game of GAMES) {
    const row = template.content.firstElementChild.cloneNode(true);
    translatePage(row);
    row.dataset.game = game;
    row.querySelector(".game-name").textContent = t(`game.${game}`);
    row.querySelector(".game-about").textContent = t(`admin.game_about_${game}`);
    box.append(row);
  }
}

function renderGamesForm(games) {
  $("games-minutes").value = String(games.daily_minutes);
  for (const row of $("game-rows").children) {
    const item = games.items[row.dataset.game];
    row.querySelector(".game-enabled").checked = item.enabled;
    row.querySelector(".game-level").value = String(item.level);
  }
  danceTile = games.dance_tile;
  renderDanceOptions(false);
}

function renderDanceOptions(keepChoice = true) {
  const select = $("dance-tile");
  // Keep an unsaved choice when only the tile list changed.
  const chosen = keepChoice && select.options.length ? select.value : danceTile;
  const current = Object.assign(document.createElement("option"), { value: "", textContent: t("admin.dance_current") });
  select.replaceChildren(
    current,
    ...knownTiles.map((tile) => Object.assign(document.createElement("option"), { value: tile.id, textContent: tile.title })),
  );
  select.value = knownTiles.some((tile) => tile.id === chosen) ? chosen : "";
}

async function loadCredits() {
  const result = await guarded(() => get("/api/credits"));
  if (!result) return;
  $("credits-list").replaceChildren(
    ...result.data.credits.map((item) => {
      const entry = document.createElement("li");
      const name = item.name === "pictures" ? t("admin.credit_pictures") : t(`sound.${item.name}`);
      const source = Object.assign(document.createElement("a"), { href: item.source, textContent: name, rel: "noopener" });
      const licence = Object.assign(document.createElement("a"), {
        href: item.license_url,
        textContent: item.license,
        rel: "noopener",
      });
      entry.append(source, ` – ${item.author}, `, licence);
      return entry;
    }),
  );
}

function gamesFromForm() {
  const items = {};
  for (const row of $("game-rows").children) {
    items[row.dataset.game] = {
      enabled: row.querySelector(".game-enabled").checked,
      level: Number(row.querySelector(".game-level").value),
    };
  }
  return { daily_minutes: $("games-minutes").valueAsNumber, dance_tile: $("dance-tile").value || null, items };
}

function renderSleepForm(timer) {
  $("sleep-enabled").checked = timer.enabled;
  $("sleep-minutes").value = String(timer.minutes);
  $("sleep-wake").value = timer.wake;
}

function renderSleepStatus(timer) {
  const running = Boolean(timer.ends_at);
  $("sleep-status").hidden = !running;
  $("sleep-cancel").hidden = !running;
  if (running) $("sleep-status").textContent = t("admin.sleep_running", { time: clockText(timer.ends_at) });
}

// -- usage times ------------------------------------------------------------------------

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
let serverZone = null;

function buildDays() {
  const box = $("days");
  const template = $("day-row");
  for (const day of DAYS) {
    const row = template.content.firstElementChild.cloneNode(true);
    translatePage(row);
    row.dataset.day = day;
    row.querySelector(".day").textContent = t(`admin.day_${day}`);
    const free = row.querySelector(".free");
    free.addEventListener("change", () => updateDayRow(row));
    box.append(row);
  }
}

function updateDayRow(row) {
  const free = row.querySelector(".free").checked;
  for (const input of row.querySelectorAll("input[type=time]")) input.disabled = free;
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

// Times are shown in the Muckebox's zone: that is the one the usage times use.
function clockText(epoch) {
  const options = { weekday: "short", hour: "2-digit", minute: "2-digit" };
  try {
    return new Intl.DateTimeFormat("de-DE", { ...options, timeZone: serverZone || undefined }).format(
      new Date(epoch * 1000),
    );
  } catch {
    return new Intl.DateTimeFormat("de-DE", options).format(new Date(epoch * 1000)); // unknown zone name
  }
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
  $("override-buttons").hidden = schedule.phase === "off"; // nothing to release
  // While an ended override fades out there is nothing left to end.
  $("override-end").hidden = !schedule.override_until || schedule.phase === "fading";
}

async function override(value, button) {
  const body = value === "morning" ? { until: "morning" } : { minutes: Number(value) };
  const result = await busy(button, guarded(() => post("/api/admin/override", body)));
  if (result) renderScheduleStatus(result.data.schedule);
}

async function loadFavorites(refresh = false) {
  if (!settings || !settings.room) {
    $("favorites").replaceChildren(); // without a room there are no favorites to show
    return;
  }
  $("favorites").replaceChildren(Object.assign(document.createElement("li"), { textContent: t("admin.loading") }));
  const result = await guarded(() => get(`/api/admin/favorites${refresh ? "?refresh=1" : ""}`, { timeout: 20000 }));
  if (result) renderFavorites(result.data.favorites);
  else $("favorites").replaceChildren();
}

async function showApp() {
  showOnly("app");
  $("rooms").replaceChildren();
  await loadSettings();
  const first = settings && !settings.room;
  await Promise.all([loadStatus(), loadTiles(), loadFavorites(), first ? searchRooms(false) : null]);
}

// -- wiring -------------------------------------------------------------------------

function bind() {
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
  $("volume").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = { max_volume: $("max-volume").valueAsNumber, volume_step: $("volume-step").valueAsNumber };
    const result = await busy(
      event.target.querySelector("button"),
      guarded(() => request("PUT", "/api/admin/settings/volume", { body }), { success: t("admin.saved") }),
    );
    if (result) {
      renderSettings(result.data);
      loadStatus();
    }
  });
  $("controls").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = {
      tap_cooldown: $("tap-cooldown").valueAsNumber,
      idle_minutes: $("idle-minutes").valueAsNumber,
    };
    const result = await busy(
      event.target.querySelector("button"),
      guarded(() => request("PUT", "/api/admin/settings/controls", { body }), { success: t("admin.saved") }),
    );
    if (result) {
      renderSettings(result.data);
      loadStatus();
    }
  });
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
  $("schedule").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await busy(
      event.target.querySelector("button[type=submit]"),
      guarded(() => request("PUT", "/api/admin/settings/schedule", { body: scheduleFromForm() }), {
        success: t("admin.saved"),
      }),
    );
    if (!result) return;
    renderScheduleForm(result.data.settings.schedule);
    loadStatus();
  });
  buildGameRows();
  $("credits").addEventListener("toggle", loadCredits, { once: true });
  $("games").addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await busy(
      event.target.querySelector("button[type=submit]"),
      guarded(() => request("PUT", "/api/admin/settings/games", { body: gamesFromForm() }), {
        success: t("admin.saved"),
      }),
    );
    if (!result) return;
    renderGamesForm(result.data.settings.games);
    loadStatus();
  });
  $("sleep-timer").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = {
      enabled: $("sleep-enabled").checked,
      minutes: $("sleep-minutes").valueAsNumber,
      wake: $("sleep-wake").value,
    };
    const result = await busy(
      event.target.querySelector("button[type=submit]"),
      guarded(() => request("PUT", "/api/admin/settings/sleep-timer", { body }), { success: t("admin.saved") }),
    );
    if (result) renderSleepForm(result.data.settings.sleep_timer);
  });
  $("sleep-cancel").addEventListener("click", async (event) => {
    const result = await busy(event.target, guarded(() => request("DELETE", "/api/admin/sleep-timer")));
    if (result) renderSleepStatus(result.data.sleep_timer);
  });
  for (const button of document.querySelectorAll("[data-override]")) {
    button.addEventListener("click", () => override(button.dataset.override, button));
  }
  $("override-end").addEventListener("click", async (event) => {
    const result = await busy(event.target, guarded(() => request("DELETE", "/api/admin/override")));
    if (result) renderScheduleStatus(result.data.schedule);
  });
  $("pin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("pin-new").value !== $("pin-repeat").value) {
      flash(t("admin.pin_mismatch"), true);
      return;
    }
    const body = { current: $("pin-current").value, new: $("pin-new").value };
    const result = await busy(
      event.target.querySelector("button"),
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
  const result = await guarded(() => get("/api/admin/session"));
  if (!result) return;
  if (result.data.logged_in) showApp();
  else showLogin();
}

start();
