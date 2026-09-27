// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// UI texts come from the server's catalogue, embedded in the page as JSON.

let messages = {};

export function loadMessages(source = document.getElementById("i18n")) {
  messages = source ? JSON.parse(source.textContent) : {};
  return messages;
}

export function setMessages(catalogue) {
  messages = catalogue;
}

/** Translate a key; `{name}` placeholders are filled from `params`. */
export function t(key, params = {}) {
  const text = Object.hasOwn(messages, key) ? messages[key] : key;
  return text.replace(/\{([a-z_][a-z0-9_]*)\}/g, (match, name) =>
    Object.hasOwn(params, name) ? String(params[name]) : match,
  );
}

/**
 * Fill elements marked with data-i18n (text), data-i18n-label (aria-label)
 * and data-i18n-placeholder (placeholder).
 */
export function translatePage(root = document) {
  for (const element of root.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of root.querySelectorAll("[data-i18n-label]")) {
    element.setAttribute("aria-label", t(element.dataset.i18nLabel));
  }
  for (const element of root.querySelectorAll("[data-i18n-placeholder]")) {
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder));
  }
}
