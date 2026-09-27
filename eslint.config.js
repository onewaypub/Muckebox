// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Lints the browser code and checks it against the supported browsers
// (see "browserslist" in package.json).

import js from "@eslint/js";
import compat from "eslint-plugin-compat";
import globals from "globals";

export default [
  js.configs.recommended,
  compat.configs["flat/recommended"],
  {
    files: ["muckebox/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
  },
  {
    files: ["tests/js/**/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.node,
    },
  },
];
