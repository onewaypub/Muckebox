// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Bedtime on the kids view: the moon instead of the tiles, the small moon
// (sleep timer, fading), and the parents' PIN pad behind a 3-second press.

import { ApiError, post, serverNow } from "./api.js";
import { t } from "./i18n.js";
import { PAD_TRIES, isBedtime, moonMode, padPress, padReady, sleepLeft } from "./logic.js";

const HOLD_MS = 3000;
const IDLE_MS = 30000;
const LOCK_MS = 60000;

const view = {
  panel: document.getElementById("bedtime"),
  moon: document.getElementById("moon"),
  until: document.getElementById("bedtime-until"),
  smallMoon: document.getElementById("small-moon"),
  ring: document.getElementById("ring-fill"),
  pad: document.getElementById("pin-pad"),
  dots: document.getElementById("pad-dots"),
  keys: document.getElementById("pad-keys"),
  message: document.getElementById("pad-message"),
  close: document.getElementById("pad-close"),
};

const pad = { pin: "", wrong: 0, lockedUntil: 0, idleTimer: null, busy: false };
const moon = { mode: "hidden", timer: null, state: null, held: false };
let changed = () => {};

export function padIsOpen() {
  return !view.pad.hidden;
}

/** Wire up the moon, the small moon and the pad; ``onChange`` polls the state soon. */
export function initBedtime({ onChange }) {
  changed = onChange;
  holdToOpen(view.moon);
  holdToOpen(view.smallMoon);
  view.smallMoon.addEventListener("click", startSleepTimer);
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0"];
  view.keys.replaceChildren(
    ...keys.map((key) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = key === "back" ? "pad-key pad-back" : "pad-key";
      button.textContent = key === "back" ? "⌫" : key;
      if (key === "back") button.setAttribute("aria-label", t("kids.pad_delete"));
      button.addEventListener("click", () => press(key));
      return button;
    }),
  );
  for (const button of view.pad.querySelectorAll("[data-choice]")) {
    button.addEventListener("click", () => submit(button.dataset.choice));
  }
  view.close.addEventListener("click", closePad);
}

/** Show or hide the moon; returns true when it replaces the tiles. */
export function renderBedtime(state) {
  const bedtime = isBedtime(state);
  view.panel.hidden = !bedtime;
  moon.state = state;
  moon.mode = moonMode(state);
  view.smallMoon.hidden = moon.mode === "hidden";
  view.smallMoon.dataset.mode = moon.mode;
  renderRing();
  clearInterval(moon.timer);
  if (moon.mode === "running") moon.timer = setInterval(renderRing, 10000);
  const opens = bedtime && state.schedule.opens_at;
  view.until.textContent = opens ? t("kids.bedtime_until", { time: clock(opens, state.schedule.zone) }) : "";
  return bedtime;
}

function renderRing() {
  const timer = moon.state && moon.state.sleep_timer;
  const left = moon.mode === "running" ? sleepLeft(timer.ends_at, timer.minutes, serverNow()) : 0;
  view.ring.style.strokeDashoffset = String(100 - left * 100);
}

async function startSleepTimer() {
  if (moon.held) {
    moon.held = false; // the end of a long press is not a tap
    return;
  }
  if (moon.mode !== "start") return;
  moon.mode = "running"; // feedback right away; the next poll brings the ring
  view.smallMoon.dataset.mode = "running";
  try {
    await post("/api/sleep-timer/start");
  } catch {
    // Not started (e.g. a Wi-Fi hiccup): show the button again, so that the
    // kid can try once more; the state itself did not change.
    moon.mode = moonMode(moon.state);
    view.smallMoon.dataset.mode = moon.mode;
  }
  changed();
}

function clock(epoch, zone) {
  const options = { hour: "2-digit", minute: "2-digit" };
  try {
    return new Intl.DateTimeFormat("de-DE", { ...options, timeZone: zone || undefined }).format(new Date(epoch * 1000));
  } catch {
    // A zone name this browser does not know: the tablet's own time instead.
    return new Intl.DateTimeFormat("de-DE", options).format(new Date(epoch * 1000));
  }
}

// A long press, so that kids do not open it by chance; parents hold 3 s.
function holdToOpen(element) {
  let timer = null;
  const cancel = () => {
    clearTimeout(timer);
    timer = null;
    element.classList.remove("holding");
  };
  element.addEventListener("pointerdown", (event) => {
    cancel();
    moon.held = false; // a new press: an old hold must not swallow this tap
    // Only one finger: two small hands on the moon must not open the pad.
    if (!event.isPrimary || Date.now() < pad.lockedUntil) return;
    element.classList.add("holding");
    timer = setTimeout(() => {
      cancel();
      moon.held = true;
      openPad();
    }, HOLD_MS);
  });
  for (const type of ["pointerup", "pointercancel", "pointerleave"]) {
    element.addEventListener(type, cancel);
  }
}

function openPad() {
  pad.pin = "";
  showMessage("");
  renderPad();
  view.pad.hidden = false;
  touch();
}

function closePad() {
  view.pad.hidden = true;
  pad.pin = "";
  clearTimeout(pad.idleTimer);
}

function touch() {
  clearTimeout(pad.idleTimer);
  pad.idleTimer = setTimeout(closePad, IDLE_MS);
}

function press(key) {
  pad.pin = padPress(pad.pin, key);
  showMessage("");
  renderPad();
  touch();
}

function renderPad() {
  view.dots.textContent = "●".repeat(pad.pin.length) || " ";
  for (const button of view.pad.querySelectorAll("[data-choice]")) {
    button.disabled = pad.busy || !padReady(pad.pin);
  }
}

function showMessage(text) {
  view.message.textContent = text;
  view.message.hidden = !text;
}

async function submit(choice) {
  if (pad.busy || !padReady(pad.pin)) return; // a double tap must not give twice the time
  pad.busy = true;
  renderPad();
  const body = choice === "morning" ? { pin: pad.pin, until: "morning" } : { pin: pad.pin, minutes: Number(choice) };
  touch();
  try {
    await post("/api/override", body);
    pad.wrong = 0;
    closePad();
    changed();
  } catch (error) {
    pad.pin = "";
    renderPad();
    const status = error instanceof ApiError ? error.status : 0;
    if (status === 401 && ++pad.wrong < PAD_TRIES) {
      showMessage(t("kids.pad_wrong"));
      return;
    }
    if (status === 401 || status === 429) {
      pad.wrong = 0;
      pad.lockedUntil = Date.now() + LOCK_MS;
      showMessage(t("kids.pad_locked"));
      setTimeout(closePad, 2500);
      return;
    }
    showMessage(t(error instanceof ApiError ? `error.${error.code}` : "kids.error"));
  } finally {
    pad.busy = false;
    renderPad();
  }
}
