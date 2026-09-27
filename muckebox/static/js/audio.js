// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Sounds and the voice of the games, from the tablet itself.
//
// iPads play sound and speech only after a tap: unlockAudio() must run
// inside the tap that starts a game. One <audio> element is reused for all
// sounds, because iOS unlocks elements, not the page. Every game also works
// without a voice (some Android WebViews have none).

import { soundUrl } from "./catalogue.js";

const LONGEST_SOUND_MS = 8000;
const player = new Audio();
player.preload = "auto";

/** Call inside the tap that starts a game. */
export function unlockAudio() {
  player.src = soundUrl("silence");
  player.play().catch(() => {});
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(" "));
  }
}

/** Play a sound; resolves when it has ended (or failed). */
export function playSound(id) {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      player.onended = player.onerror = null;
      resolve();
    };
    const timer = setTimeout(done, LONGEST_SOUND_MS);
    player.onended = player.onerror = done;
    player.src = soundUrl(id);
    player.play().catch(done);
  });
}

function germanVoice() {
  const voices = window.speechSynthesis.getVoices();
  return voices.find((voice) => voice.lang === "de-DE") || voices.find((voice) => voice.lang.startsWith("de"));
}

/** Say ``text`` in German; resolves when done (or at once without a voice). */
export function speak(text) {
  return new Promise((resolve) => {
    const estimate = 500 + text.length * 70;
    if (!window.speechSynthesis) {
      // Fully Kiosk Browser on Android brings its own text to speech.
      if (window.fully && window.fully.textToSpeech) window.fully.textToSpeech(text, "de_DE");
      setTimeout(resolve, estimate);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "de-DE";
    utterance.rate = 0.9;
    const voice = germanVoice();
    if (voice) utterance.voice = voice;
    const timer = setTimeout(resolve, estimate + 4000); // some engines never call onend
    utterance.onend = utterance.onerror = () => {
      clearTimeout(timer);
      resolve();
    };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  });
}

export function stopAudio() {
  player.pause();
  if (window.speechSynthesis) window.speechSynthesis.cancel();
}
