// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Touch diagnosis: while the parents run it, the tablet shows every touch as
// a dot (green: down, blue: up, red: cancelled by the browser) and sends what
// arrived to Muckebox: pointer, touch, click and long-press events, and what
// the tiles made of them (diagNote). Nothing is stored on the tablet.

import { ApiError, post } from "./api.js";

const SEND_MS = 2000;
const MOVE_MS = 80; // at most one move entry per finger in this time
const MAX_QUEUE = 1000;
const DOT_MS = 4000;
const POINTER = ["pointerdown", "pointermove", "pointerup", "pointercancel", "lostpointercapture"];
const OTHER = ["touchstart", "touchend", "touchcancel", "contextmenu", "click"];

let active = false;
let queue = [];
let sendTimer = null;
let overlay = null;
const downs = new Map(); // pointerId -> {x, y, at, moveAt, lastX, lastY}

export function touchDiagActive() {
  return active;
}

/** Switch the diagnosis on or off (the server says so in the state). */
export function setTouchDiag(on) {
  if (on === active) return;
  active = on;
  for (const type of [...POINTER, ...OTHER]) {
    if (on) document.addEventListener(type, onEvent, { capture: true, passive: true });
    else document.removeEventListener(type, onEvent, { capture: true });
  }
  if (on) {
    overlay = document.createElement("div");
    overlay.className = "touch-diag";
    overlay.append(Object.assign(document.createElement("span"), { className: "touch-diag-label", textContent: "Touch-Diagnose" }));
    document.body.append(overlay);
    record({ type: "page", w: window.innerWidth, h: window.innerHeight });
    sendTimer = setInterval(send, SEND_MS);
  } else {
    clearInterval(sendTimer);
    send();
    if (overlay) overlay.remove();
    overlay = null;
    downs.clear();
  }
}

/** What the tiles made of a press (e.g. "tile-hold"), with details. */
export function diagNote(type, details = {}) {
  if (active) record({ type, ...details });
}

function record(entry) {
  if (queue.length >= MAX_QUEUE) queue.shift();
  queue.push({ t: Math.round(performance.now()), ...entry });
}

async function send() {
  if (!queue.length) return;
  const batch = queue.splice(0, 200);
  try {
    await post("/api/diag/touch", { events: batch });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) setTouchDiag(false); // switched off
  }
}

/** A short name of what was hit: "tile:3", "light:1", "control:toggle", "gap", "other". */
export function targetName(element) {
  if (!(element instanceof Element)) return "other";
  const tile = element.closest(".tile");
  if (tile) return `tile:${[...document.querySelectorAll(".tile")].indexOf(tile) + 1}`;
  const light = element.closest(".light");
  if (light) return `light:${light.dataset.slot}`;
  const button = element.closest("button[id]");
  if (button) return `control:${button.id}`.slice(0, 40);
  if (element.closest(".tiles")) return "gap";
  return "other";
}

function onEvent(event) {
  const type = event.type;
  if (type.startsWith("touch")) {
    record({ type, touches: event.touches.length, target: targetName(event.target) });
    return;
  }
  const entry = { type, x: Math.round(event.clientX), y: Math.round(event.clientY), target: targetName(event.target) };
  if (type === "contextmenu" || type === "click") {
    record(entry);
    return;
  }
  entry.id = event.pointerId;
  entry.pt = event.pointerType;
  const now = performance.now();
  if (type === "pointerdown") {
    downs.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
      at: now,
      moveAt: 0,
      lastX: event.clientX,
      lastY: event.clientY,
    });
    dot(event.clientX, event.clientY, "down");
    record(entry);
    return;
  }
  const down = downs.get(event.pointerId);
  // A cancelled pointer reports 0,0: then the last position of the finger counts.
  const placed = type !== "pointercancel" && type !== "lostpointercapture";
  if (down && placed) {
    down.lastX = event.clientX;
    down.lastY = event.clientY;
  }
  if (down) {
    entry.x = Math.round(down.lastX);
    entry.y = Math.round(down.lastY);
    entry.moved = Math.round(Math.hypot(down.lastX - down.x, down.lastY - down.y));
    entry.ms = Math.round(now - down.at);
  }
  if (type === "pointermove") {
    if (!down || now - down.moveAt < MOVE_MS) return; // only while pressed, not too often
    down.moveAt = now;
    record(entry);
    return;
  }
  if (type === "lostpointercapture") {
    record(entry); // comes after up or cancel: the press stays known until then
    return;
  }
  downs.delete(event.pointerId);
  if (down) dot(down.lastX, down.lastY, type === "pointerup" ? "up" : "cancel");
  record(entry);
}

function dot(x, y, kind) {
  if (!overlay) return;
  const mark = document.createElement("i");
  mark.className = `touch-dot ${kind}`;
  mark.style.left = `${x}px`;
  mark.style.top = `${y}px`;
  overlay.append(mark);
  setTimeout(() => mark.remove(), DOT_MS);
}
