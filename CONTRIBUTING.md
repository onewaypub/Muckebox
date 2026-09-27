<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Contributing to Muckebox

Thank you for helping! Bug reports, translations, documentation and code are
all welcome.

## Ground rules

- Code, comments, commit messages and documentation are written in
  **English**. User interface texts come from the i18n catalogue
  (`muckebox/i18n/`); never hard-code UI text.
- Every source file starts with an SPDX header:

  ```text
  SPDX-FileCopyrightText: 2026 Muckebox contributors
  SPDX-License-Identifier: AGPL-3.0-or-later
  ```

  (as a comment in the file's own syntax). By contributing you agree that
  your contribution is licensed under AGPL-3.0-or-later.
- **No private data in the repository.** Never commit real IP addresses, room
  names, Sonos household or player IDs, account serials, PINs, email
  addresses or dumps from your own Sonos system. Use the documentation
  address ranges `192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24` and
  obvious placeholders in examples and test fixtures. A secret scan with
  extra rules for this runs locally and in CI.
- Only copy code from projects whose license is compatible with
  AGPL-3.0-or-later, and keep their notices.
- The browser baseline is **iOS/iPadOS 16.4+ and Chrome/WebView 111+**,
  without a build step. Lint checks enforce it.

## Development setup

Requires Python 3.13+ (and Node.js for the frontend checks once they exist).

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
```

## Checks

Run all checks before every commit:

```sh
scripts/check
```

It runs ruff (lint and format check), pytest with coverage, the license
header check, bandit, pip-audit and gitleaks. The same checks run in CI for
every pull request.

## Dependencies

Runtime dependencies are pinned with hashes. To change them, edit
`requirements.in` (or `requirements-dev.in`) and regenerate the lock files
with `scripts/lock` (runs pip-compile inside the Python 3.13 image used in
production). New runtime dependencies must have an AGPL-compatible license
and must be added to the dependency table in the README.

## Commits and pull requests

- Keep commits small and focused; use [Conventional Commits](https://www.conventionalcommits.org/)
  prefixes (`feat:`, `fix:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`).
- Every commit should pass `scripts/check`.
- Describe in the pull request how you tested the change, including manual
  tests on real hardware where relevant.
