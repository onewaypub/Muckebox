// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Pure game logic (tested with `node --test`): rounds, sequences and
// schedules for the three levels (1 small, 2 middle, 3 big kids).

import { ANIMALS, EVERYDAY, MOVES } from "./catalogue.js";

/** A small seeded random generator (mulberry32): same seed, same game. */
export function randomFrom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let x = state;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(list, random) {
  return list[Math.floor(random() * list.length)];
}

function shuffled(list, random) {
  const copy = [...list];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

// -- breathing ------------------------------------------------------------------

/** Breathing exercise: seconds in and out, how long, how many breaths are spoken. */
export function breathingPlan(level) {
  const [inhale, exhale, minutes] = [
    [3, 4, 3],
    [4, 5, 4],
    [4, 6, 5],
  ][level - 1];
  const cycles = Math.floor((minutes * 60) / (inhale + exhale));
  return { inhale, exhale, cycles, guided: 3 };
}

/** How dark the screen is after ``done`` of ``cycles`` breaths (0 = normal). */
export function dimLevel(done, cycles) {
  return Math.min(0.9, (done / Math.max(1, cycles)) * 0.9);
}

// -- sound quiz --------------------------------------------------------------------

export const QUIZ_ROUNDS = 5;

/** Rounds of the quiz: the sound to guess and the pictures to choose from. */
export function quizRounds(level, random) {
  const pool = level === 3 ? [...ANIMALS, ...EVERYDAY] : ANIMALS;
  const choices = level + 1; // 2, 3 or 4 pictures
  const rounds = [];
  let previous = null;
  for (let round = 0; round < QUIZ_ROUNDS; round += 1) {
    let answer = pick(pool, random);
    while (answer === previous) answer = pick(pool, random);
    previous = answer;
    const others = shuffled(
      pool.filter((id) => id !== answer),
      random,
    ).slice(0, choices - 1);
    rounds.push({ answer, choices: shuffled([answer, ...others], random) });
  }
  return rounds;
}

// -- move like ... ------------------------------------------------------------------

/** "Beweg dich wie …": which animals, and how long each (fewer, slower for small kids). */
export function moveSequence(level, random) {
  const [count, seconds] = [
    [4, 30],
    [6, 25],
    [8, 20],
  ][level - 1];
  return shuffled(MOVES, random)
    .slice(0, count)
    .map((move) => ({ ...move, seconds }));
}

// -- freeze dance ------------------------------------------------------------------

const FREEZE = {
  1: { dance: [8, 15], freeze: [4, 6] },
  2: { dance: [5, 12], freeze: [3, 5] },
  3: { dance: [3, 8], freeze: [3, 4] },
};
/** The music needs a moment to start; the first dance is at least this long. */
export const FIRST_DANCE = 12;

/**
 * The freeze dance: alternating "dance" and "freeze" phases filling
 * ``seconds``; it starts and ends with dancing (music on).
 */
export function freezeSchedule(level, seconds, random) {
  const { dance, freeze } = FREEZE[level];
  const between = ([low, high]) => low + Math.round(random() * (high - low));
  const phases = [];
  let left = seconds;
  let dancing = true;
  while (left > 0) {
    let length = dancing ? between(dance) : between(freeze);
    if (phases.length === 0) length = Math.max(length, FIRST_DANCE);
    if (!dancing && left - length < dance[0]) {
      // Not enough time left for a freeze and a last dance: dance to the end.
      phases[phases.length - 1].seconds += left;
      break;
    }
    length = Math.min(length, left);
    phases.push({ phase: dancing ? "dance" : "freeze", seconds: length });
    left -= length;
    dancing = !dancing;
  }
  return phases;
}
