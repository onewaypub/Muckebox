// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later

import assert from "node:assert/strict";
import { test } from "node:test";

import { ANIMALS, EVERYDAY } from "../../muckebox/static/js/catalogue.js";
import {
  FIRST_DANCE,
  QUIZ_ROUNDS,
  breathingPlan,
  dimLevel,
  freezeSchedule,
  moveSequence,
  quizRounds,
  randomFrom,
} from "../../muckebox/static/js/gamelogic.js";

test("the random generator repeats for the same seed", () => {
  const a = randomFrom(42);
  const b = randomFrom(42);
  const values = [a(), a(), a()];
  assert.deepEqual(values, [b(), b(), b()]);
  assert.ok(values.every((value) => value >= 0 && value < 1));
});

test("breathing gets slower and longer for bigger kids, and the screen darkens", () => {
  assert.deepEqual(breathingPlan(1), { inhale: 3, exhale: 4, cycles: 25, guided: 3 });
  assert.equal(breathingPlan(3).exhale, 6);
  assert.equal(dimLevel(0, 20), 0);
  assert.equal(dimLevel(20, 20), 0.9);
  assert.equal(dimLevel(30, 20), 0.9);
});

test("quiz: 2, 3 or 4 different pictures, the answer among them, no repeats in a row", () => {
  for (const level of [1, 2, 3]) {
    for (let seed = 1; seed < 30; seed += 1) {
      const rounds = quizRounds(level, randomFrom(seed));
      assert.equal(rounds.length, QUIZ_ROUNDS);
      rounds.forEach((round, index) => {
        assert.equal(round.choices.length, level + 1);
        assert.equal(new Set(round.choices).size, round.choices.length);
        assert.ok(round.choices.includes(round.answer));
        if (index > 0) assert.notEqual(round.answer, rounds[index - 1].answer);
        if (level < 3) assert.ok(round.choices.every((id) => ANIMALS.includes(id)));
      });
    }
  }
  const big = quizRounds(3, randomFrom(7)).flatMap((round) => round.choices);
  assert.ok(big.every((id) => ANIMALS.includes(id) || EVERYDAY.includes(id)));
});

test("move like: 4 slow to 8 quicker animals, all different", () => {
  const small = moveSequence(1, randomFrom(3));
  const big = moveSequence(3, randomFrom(3));
  assert.deepEqual([small.length, small[0].seconds], [4, 30]);
  assert.deepEqual([big.length, big[0].seconds], [8, 20]);
  assert.equal(new Set(big.map((move) => move.id)).size, 8);
});

test("freeze dance: alternates, fills the round, starts and ends with dancing", () => {
  for (const level of [1, 2, 3]) {
    for (let seed = 1; seed < 40; seed += 1) {
      for (const seconds of [60, 120, 180, 240]) {
        const phases = freezeSchedule(level, seconds, randomFrom(seed));
        assert.equal(phases.reduce((sum, phase) => sum + phase.seconds, 0), seconds);
        assert.equal(phases[0].phase, "dance");
        assert.ok(phases[0].seconds >= FIRST_DANCE);
        assert.equal(phases.at(-1).phase, "dance");
        phases.forEach((phase, index) => {
          if (index > 0) assert.notEqual(phase.phase, phases[index - 1].phase);
          if (phase.phase === "freeze") assert.ok(phase.seconds >= 3 && phase.seconds <= 6);
        });
      }
    }
  }
});
