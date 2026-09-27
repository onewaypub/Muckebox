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
  without a build step. ESLint (`eslint-plugin-compat`) and Stylelint
  (`stylelint-no-unsupported-browser-features`) check the browser code
  against it (`npm ci` once, then `npm run lint`). `touch-action` is
  exempt: iOS Safari supports it; only desktop Safari lacks it, where it
  does not matter.

## Development setup

Requires Python 3.13+ and Node.js 24+ (only for the frontend checks).

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
npm ci                                         # browser linters (dev only)
python -m playwright install webkit chromium   # optional: browser tests
```

Run Muckebox without a speaker (a simulated one with demo favorites):

```sh
MUCKEBOX_FAKE_SONOS=1 DATA_DIR=./data python -m muckebox
```

Add `LISTEN=localhost` to keep it reachable from this computer only. The log
shows the PIN for the first login. Open <http://localhost:8484/admin>
(parents' page), log in and choose a room of the simulated household
("Kinderzimmer" or "Wohnzimmer"); the kids view is at
<http://localhost:8484/>.

## Checks

Run all checks before every commit:

```sh
scripts/check
```

It runs ruff (lint and format check), pytest with coverage (including the
license header check), bandit, pip-audit and the privacy scan. The same
checks run in CI for every pull request. `scripts/check --fast` skips
pip-audit.

The privacy scan (`scripts/privacy_scan.py`, rules in `.gitleaks.toml`)
checks the git history, the working tree, commit messages and commit email
addresses for secrets and private data. `scripts/check` downloads the pinned
gitleaks binary to `.tools/` on first use.

Commits must use your GitHub login as name and your GitHub noreply address
as email. Otherwise git records the full name of your operating system
account and your private email address, and the history would publish them:

```sh
git config user.name "<username>"
git config user.email "<id>+<username>@users.noreply.github.com"
```

## Dependencies

Runtime dependencies are pinned with hashes. To change them, edit
`requirements.in` (or `requirements-dev.in`) and regenerate the lock files
with `scripts/lock` (runs pip-compile inside the Python 3.13 image used in
production). New runtime dependencies must have an AGPL-compatible license
and must be added to the dependency table in the README.

## Commits and pull requests

Maintainers: merge pull requests with *Rebase and merge*. A squash or merge
commit made in the GitHub web interface is authored with the account's
profile name and email, which the privacy checks cannot review beforehand.
Keep the account's email private and its display name equal to the login.

- Keep commits small and focused; use [Conventional Commits](https://www.conventionalcommits.org/)
  prefixes (`feat:`, `fix:`, `docs:`, `test:`, `build:`, `ci:`, `chore:`).
- Every commit should pass `scripts/check`.
- Describe in the pull request how you tested the change, including manual
  tests on real hardware where relevant.
