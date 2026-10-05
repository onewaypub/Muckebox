// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Instant feedback for small children: a soft "plop" from the tablet and a
// little hop of the picture, the moment a tap starts something. The speaker
// needs a second or two to start; without this a tap feels like nothing.
//
// The sound is made with Web Audio (no file). Tablets allow sound only after
// a touch, so unlockFeedback() runs at every touch, plop() may come later.

let context = null;
let enabled = true;

export function setTapSound(on) {
  enabled = on;
}

/** Call at the touch itself (pointerdown): allows sound on iPads. */
export function unlockFeedback() {
  if (!enabled) return;
  try {
    context = context || new AudioContext();
    if (context.state === "suspended") context.resume().catch(() => {});
  } catch {
    context = null; // no Web Audio: the hop still shows
  }
}

/** A short, soft, falling tone. */
export function plop() {
  if (!enabled || !context || context.state !== "running") return;
  const now = context.currentTime;
  const tone = context.createOscillator();
  const gain = context.createGain();
  tone.type = "sine";
  tone.frequency.setValueAtTime(660, now);
  tone.frequency.exponentialRampToValueAtTime(260, now + 0.12);
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.25, now + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
  tone.connect(gain).connect(context.destination);
  tone.start(now);
  tone.stop(now + 0.18);
}

/** Let ``node`` hop once (the CSS animation "hop"). */
export function hop(node) {
  if (!node) return;
  node.classList.remove("hop");
  void node.offsetWidth; // restart the animation
  node.classList.add("hop");
  node.addEventListener("animationend", () => node.classList.remove("hop"), { once: true });
}
