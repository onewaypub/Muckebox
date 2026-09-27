// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The games on the kids view: the games button, the picker and the four
// games. Short rounds with a calm end, no points, no endless mode. The
// server decides whether a game may start and for how long.

import { ApiError, post } from "./api.js";
import { playSound, speak, stopAudio, unlockAudio } from "./audio.js";
import { pictureUrl } from "./catalogue.js";
import { breathingPlan, dimLevel, freezeSchedule, moveSequence, quizRounds, randomFrom } from "./gamelogic.js";
import { t } from "./i18n.js";
import { isBedtime } from "./logic.js";

const view = {
  button: document.getElementById("games-button"),
  bedtimeBreathing: document.getElementById("bedtime-breathing"),
  layer: document.getElementById("game-layer"),
  close: document.getElementById("game-close"),
  picker: document.getElementById("game-picker"),
  stage: document.getElementById("game-stage"),
};

let items = [];
let running = null; // AbortController of the running game
let changed = () => {};
let failed = () => {};

export function gameIsOpen() {
  return !view.layer.hidden;
}

/** ``onChange`` polls soon; ``onError`` shows a short error picture. */
export function initGames({ onChange, onError }) {
  changed = onChange;
  failed = onError;
  view.button.addEventListener("click", openPicker);
  view.bedtimeBreathing.addEventListener("click", () => launch("breathing"));
  view.close.addEventListener("click", closeLayer);
}

export function renderGames(state) {
  items = (state && state.games && state.games.items) || [];
  const bedtime = isBedtime(state);
  view.button.hidden = bedtime || !items.some((item) => item.available);
  view.bedtimeBreathing.hidden = !bedtime || !items.some((item) => item.id === "breathing");
  if (!view.picker.hidden) renderPicker();
}

// -- the picker --------------------------------------------------------------------

function openPicker() {
  view.stage.hidden = true;
  view.picker.hidden = false;
  view.layer.hidden = false;
  renderPicker();
}

function renderPicker() {
  view.picker.replaceChildren(
    ...items.map((item) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "game-card";
      card.disabled = !item.available;
      card.dataset.game = item.id;
      card.append(picture(item.id), Object.assign(document.createElement("span"), { textContent: t(`game.${item.id}`) }));
      card.addEventListener("click", () => launch(item.id));
      return card;
    }),
  );
}

function closeLayer() {
  if (running) {
    running.abort(); // the game's own "finally" closes the layer
    return;
  }
  view.layer.hidden = true;
  view.picker.hidden = true;
  view.stage.replaceChildren();
}

// -- running a game -----------------------------------------------------------------

async function launch(id) {
  if (running) return;
  unlockAudio(); // inside the tap: iPads allow sound only then
  running = new AbortController();
  const { signal } = running;
  view.picker.hidden = true;
  view.stage.replaceChildren();
  view.stage.className = `game-stage game-${id}`;
  view.stage.hidden = false;
  view.layer.hidden = false;
  try {
    const { data } = await post(`/api/games/${id}/start`, undefined, { timeout: 20000 });
    await RUNNERS[id](data.game, signal);
  } catch (error) {
    if (!signal.aborted) failed(error instanceof ApiError ? error.code : "error");
  } finally {
    running = null;
    stopAudio();
    await post("/api/games/end").catch(() => {});
    closeLayer();
    changed();
  }
}

// Waits that end early when the game is closed.
function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}

function aborted(signal) {
  if (signal.aborted) throw signal.reason;
}

function picture(id, className = "game-picture") {
  return Object.assign(document.createElement("img"), { src: pictureUrl(id), alt: "", className });
}

function text(content, className = "game-text") {
  return Object.assign(document.createElement("p"), { textContent: content, className });
}

function seed() {
  return Math.floor(Math.random() * 2 ** 31);
}

// -- Atemübung (breathing) -----------------------------------------------------------

async function breathing(game, signal) {
  const plan = breathingPlan(game.level);
  const dim = Object.assign(document.createElement("div"), { className: "breathing-dim" });
  const circle = Object.assign(document.createElement("div"), { className: "breathing-circle" });
  const label = text("", "breathing-label");
  view.stage.append(circle, label, dim);
  await speak(t("game.breathing_intro"));
  for (let cycle = 0; cycle < plan.cycles; cycle += 1) {
    aborted(signal);
    const guided = cycle < plan.guided;
    circle.style.transitionDuration = `${plan.inhale}s`;
    circle.classList.add("in");
    label.textContent = guided ? t("game.breathe_in") : "";
    if (guided) speak(t("game.breathe_in"));
    await wait(plan.inhale * 1000, signal);
    circle.style.transitionDuration = `${plan.exhale}s`;
    circle.classList.remove("in");
    label.textContent = guided ? t("game.breathe_out") : "";
    if (guided) speak(t("game.breathe_out"));
    await wait(plan.exhale * 1000, signal);
    dim.style.opacity = String(dimLevel(cycle + 1, plan.cycles));
  }
  view.stage.replaceChildren(picture("moon", "game-picture night"), dim);
  await speak(t("game.good_night"));
  // It stays dark and quiet; a tap anywhere goes back.
  await new Promise((resolve, reject) => {
    view.stage.addEventListener("click", resolve, { once: true });
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

// -- Geräusche-Rätsel (sound quiz) ----------------------------------------------------

async function soundQuiz(game, signal) {
  const deadline = Date.now() + game.seconds * 1000;
  const rounds = quizRounds(game.level, randomFrom(seed()));
  const replay = Object.assign(document.createElement("button"), { type: "button", className: "quiz-replay" });
  replay.append(document.getElementById("replay-icon").content.firstElementChild.cloneNode(true));
  replay.setAttribute("aria-label", t("kids.replay"));
  const grid = Object.assign(document.createElement("div"), { className: "quiz-choices" });
  view.stage.append(replay, grid);
  await speak(t("game.quiz_listen"));
  for (const round of rounds) {
    if (Date.now() > deadline) break;
    aborted(signal);
    let answered;
    const solved = new Promise((resolve) => {
      answered = resolve;
    });
    grid.dataset.count = String(round.choices.length);
    grid.replaceChildren(
      ...round.choices.map((id) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "quiz-choice";
        button.dataset.id = id;
        button.append(picture(id));
        button.addEventListener("click", async () => {
          if (id === round.answer) {
            button.classList.add("right");
            answered();
            return;
          }
          button.classList.remove("wrong");
          void button.offsetWidth; // restart the wobble
          button.classList.add("wrong");
          await speak(t("game.quiz_again"));
          playSound(round.answer);
        });
        return button;
      }),
    );
    replay.onclick = () => playSound(round.answer);
    await playSound(round.answer);
    await Promise.race([solved, wait(120000, signal)]);
    aborted(signal);
    await speak(t("game.quiz_right", { name: t(`sound.${round.answer}`) }));
    await wait(700, signal);
  }
  grid.replaceChildren(text(t("game.quiz_done"), "game-text big"));
  await speak(t("game.quiz_done"));
  await wait(1500, signal);
}

// -- Beweg dich wie … (move like) -------------------------------------------------------

async function moveLike(game, signal) {
  const deadline = Date.now() + game.seconds * 1000;
  const holder = Object.assign(document.createElement("div"), { className: "move-holder" });
  const bar = Object.assign(document.createElement("div"), { className: "move-bar" });
  view.stage.append(holder, bar);
  for (const move of moveSequence(game.level, randomFrom(seed()))) {
    const seconds = Math.min(move.seconds, (deadline - Date.now()) / 1000 - 12);
    if (seconds < 5) break;
    aborted(signal);
    const say = t("game.move_say", { move: t(`move.${move.id}`) });
    holder.replaceChildren(picture(move.id), text(say));
    bar.style.transition = "none";
    bar.style.transform = "scaleX(1)";
    void bar.offsetWidth;
    bar.style.transition = `transform ${seconds}s linear`;
    bar.style.transform = "scaleX(0)";
    await speak(say);
    if (move.sound) playSound(move.sound);
    await wait((seconds / 2) * 1000, signal);
    if (move.sound) playSound(move.sound);
    await wait((seconds / 2) * 1000, signal);
  }
  holder.replaceChildren(picture("breathing"), text(t("game.move_calm")));
  bar.style.transition = "none";
  bar.style.transform = "scaleX(0)";
  await speak(t("game.move_calm"));
  await wait(8000, signal);
  await speak(t("game.done"));
}

// -- Stopptanz (freeze dance) -------------------------------------------------------------

async function freezeDance(game, signal) {
  const phases = freezeSchedule(game.level, game.seconds, randomFrom(seed()));
  const holder = Object.assign(document.createElement("div"), { className: "freeze-holder" });
  view.stage.append(holder);
  let failures = 0;
  const mute = async (muted) => {
    try {
      await post("/api/games/freeze_dance/mute", { muted });
      failures = 0;
    } catch (error) {
      failures += 1;
      if (failures >= 2) throw error; // the speaker does not follow: stop the game
    }
  };
  speak(t("game.freeze_ready"));
  for (const [index, phase] of phases.entries()) {
    aborted(signal);
    const dancing = phase.phase === "dance";
    view.stage.dataset.phase = phase.phase;
    // Only a calm colour and one word: the game happens in the room, not on the screen.
    holder.replaceChildren(text(t(dancing ? "game.freeze_go" : "game.freeze_stop"), "game-text big"));
    if (dancing) holder.prepend(picture("freeze_dance"));
    if (dancing && index > 0) {
      speak(t("game.freeze_go"));
      await mute(false);
    } else if (!dancing) {
      speak(t("game.freeze_stop"));
      await mute(true);
    }
    await wait(phase.seconds * 1000, signal);
  }
  delete view.stage.dataset.phase;
  holder.replaceChildren(text(t("game.done"), "game-text big"));
  await speak(t("game.done"));
}

const RUNNERS = {
  breathing,
  sound_quiz: soundQuiz,
  move_like: moveLike,
  freeze_dance: freezeDance,
};
