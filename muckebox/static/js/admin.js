// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Parents' page: log in with the PIN, manage tiles, add favorites and links.

import { ApiError, get, post, request } from "./api.js";
import { loadMessages, t, translatePage } from "./i18n.js";

const $ = (id) => document.getElementById(id);
const FLASH_MS = 5000;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
let rev = null;
let flashTimer = null;

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
  for (const section of ["locked", "login", "app"]) $(section).hidden = section !== id;
  $("logout").hidden = id !== "app";
}

function showLogin() {
  showOnly("login");
  $("pin").focus();
}

function renderStatus(status) {
  const list = $("status");
  const sonos = status.sonos || {};
  const connection =
    sonos.status === "ok" ? t("status.ok") : t(sonos.status === "starting" ? "status.starting" : `error.${sonos.status}`);
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
  const problems = [...status.config_problems.map((code) => t(`error.${code}`))];
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
    const thumb = row.querySelector(".thumb");
    if (tile.cover) thumb.src = tile.cover;
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

function renderFavorites(favorites) {
  const list = $("favorites");
  const template = $("favorite-row");
  list.replaceChildren();
  for (const favorite of favorites) {
    const row = template.content.firstElementChild.cloneNode(true);
    if (favorite.has_art) {
      row.querySelector(".thumb").src = `/api/admin/favorite-art?item_id=${encodeURIComponent(favorite.item_id)}`;
    }
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

async function loadFavorites(refresh = false) {
  $("favorites").replaceChildren(Object.assign(document.createElement("li"), { textContent: t("admin.loading") }));
  const result = await guarded(() => get(`/api/admin/favorites${refresh ? "?refresh=1" : ""}`, { timeout: 20000 }));
  if (result) renderFavorites(result.data.favorites);
  else $("favorites").replaceChildren();
}

async function showApp() {
  showOnly("app");
  await Promise.all([loadStatus(), loadTiles(), loadFavorites()]);
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
  if (result.data.locked) showOnly("locked");
  else if (result.data.logged_in) showApp();
  else showLogin();
}

start();
