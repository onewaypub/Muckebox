// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// What the games show and play: /static/sounds/<id>.mp3, /static/pictures/<id>.svg.
// Sources and licences: muckebox/assets.py (and REUSE.toml).

/** Animal sounds (all levels). */
export const ANIMALS = ["dog", "cat", "rooster", "sheep", "horse", "elephant", "lion", "donkey"];

/** Everyday sounds (only for the big kids). */
export const EVERYDAY = ["doorbell", "car_horn", "train", "clock", "water", "bicycle_bell", "church_bells", "rain"];

/** "Beweg dich wie …": an animal, and its sound if there is one. */
export const MOVES = [
  { id: "elephant", sound: "elephant" },
  { id: "cat", sound: "cat" },
  { id: "frog" },
  { id: "horse", sound: "horse" },
  { id: "lion", sound: "lion" },
  { id: "rooster", sound: "rooster" },
  { id: "bird" },
  { id: "snake" },
  { id: "turtle" },
  { id: "rabbit" },
  { id: "mouse" },
  { id: "bear" },
  { id: "butterfly" },
  { id: "penguin" },
];

export const soundUrl = (id) => `/static/sounds/${id}.mp3`;
export const pictureUrl = (id) => `/static/pictures/${id}.svg`;
