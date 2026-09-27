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
let searchSeed = null; // the speaker address the shown room list came from

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
  const list = $("status");
  const sonos = status.sonos || {};
  const own = ["ok", "starting", "not_configured"].includes(sonos.status);
  const connection = t(own ? `status.${sonos.status}` : `error.${sonos.status}`);
  const rows = [
    ["admin.room", sonos.room || "–"],
    ["admin.connection", connection],
    ["admin.max_volume", String(status.volume_guard.max)],
    ["admin.corrections", String(status.volume_guard.corrections)],
  ];
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
  $("version").textContent = `${t("admin.version")} ${status.version}`;
  $("source").href = status.source_url;
}

function renderTiles(data) {
  rev = data.rev;
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
  ]) {
    if (document.activeElement !== $(id)) $(id).value = String(value);
  }
  $("favorites-no-room").hidden = Boolean(settings.room);
  $("refresh").disabled = !settings.room;
}

function renderRooms(rooms) {
  const list = $("rooms");
  const template = $("room-row");
  list.replaceChildren();
  if (!rooms.length) {
    list.append(Object.assign(document.createElement("li"), { className: "hint", textContent: t("admin.rooms_none") }));
    return;
  }
  for (const room of rooms) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("strong").textContent = room.name;
    row.querySelector("small").textContent = room.grouped ? `${room.ip} · ${t("admin.room_grouped")}` : room.ip;
    const button = row.querySelector(".choose");
    const chosen = room.name === (settings && settings.room);
    button.textContent = t(chosen ? "admin.chosen" : "admin.choose");
    button.disabled = chosen;
    button.addEventListener("click", () => chooseRoom(room.name, rooms, button));
    list.append(row);
  }
}

async function chooseRoom(name, rooms, button) {
  const result = await busy(
    button,
    guarded(
      () =>
        request("PUT", "/api/admin/settings/room", {
          body: { room: name, seed_ip: searchSeed },
          timeout: SETUP_TIMEOUT_MS,
        }),
      { success: t("admin.room_saved", { room: name }) },
    ),
  );
  if (!result) return;
  renderSettings(result.data);
  renderRooms(rooms);
  loadStatus();
  loadFavorites();
}

async function searchRooms(refresh) {
  searchSeed = $("seed-ip").value.trim() || null;
  $("rooms").replaceChildren(Object.assign(document.createElement("li"), { textContent: t("admin.searching") }));
  const result = await guarded(() =>
    post("/api/admin/rooms/search", { seed_ip: searchSeed, refresh }, { timeout: SETUP_TIMEOUT_MS }),
  );
  if (result) renderRooms(result.data.rooms);
  else $("rooms").replaceChildren();
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
  if (result) renderSettings(result.data);
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
    busy(event.target.querySelector("button"), searchRooms(true));
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
