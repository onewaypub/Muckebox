<!--
SPDX-FileCopyrightText: 2026 Muckebox contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Security policy

## Supported versions

Muckebox is in early development. Security fixes are made for the latest
release and the `main` branch only.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Security" tab → "Report a vulnerability") of this repository.

Please include the Muckebox version, how you run it, and the steps to
reproduce the problem. Do not include private data such as your IP addresses,
room names or PIN. You should get a first answer within two weeks.

## Scope and threat model

Muckebox is designed for a trusted home network:

- It serves plain HTTP and must **not** be exposed to the internet or placed
  behind a public reverse proxy.
- The kids view has no login: anybody on the home network can start music and
  change the volume within the configured limit.
- The parents' page is protected by a PIN, rate limiting and same-origin
  checks. The PIN is stored as a salted scrypt hash. A copy of
  `settings.json` still lets an attacker try PINs offline, which is quick for
  a short numeric PIN: use a longer PIN if backups of the data folder leave
  your home. Until the parents set their own PIN, `settings.json` also holds
  the PIN Muckebox generated in plain text.
- Whoever can run commands in the container or write to the data folder can
  set a new PIN (`python -m muckebox.admin reset-pin`) and read or change the
  settings. Access to the Docker host is the trust boundary.
- Until the parents set their own PIN, the PIN Muckebox generated is written
  to the container log on every start. Anybody who can read that log can log
  in with it.
- Muckebox accepts any host name in requests. A malicious web page opened on
  a device in the home network could use DNS rebinding to reach the kids
  view's functions (start music, change the volume within the limit). The
  parents' page is not affected: its login cookie is bound to Muckebox's own
  address.
- Muckebox controls the speakers through the local Sonos UPnP interface,
  which itself has no authentication. Muckebox cannot make that interface
  more secure than it is.

Reports about these known limitations are still welcome if you see a
practical attack that the documentation does not mention.
