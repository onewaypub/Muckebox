# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the privacy rules in .gitleaks.toml.

The examples below are made up. This file is excluded from the repository
scan because it contains matching examples on purpose.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"


def find_gitleaks():
    local = ROOT / ".tools" / "bin" / "gitleaks"
    if local.is_file():
        return str(local)
    return shutil.which("gitleaks")


GITLEAKS = find_gitleaks()
pytestmark = pytest.mark.skipif(
    GITLEAKS is None and not os.environ.get("MUCKEBOX_REQUIRE_GITLEAKS"),
    reason="gitleaks not installed (run scripts/install-gitleaks)",
)


def findings(lines, tmp_path):
    """Scan all lines at once (gitleaks starts slowly); return {line_number: {rule ids}}."""
    if GITLEAKS is None:
        pytest.fail("gitleaks not found; run scripts/install-gitleaks", pytrace=False)
    report = tmp_path / "report.json"
    subprocess.run(
        [
            GITLEAKS,
            "stdin",
            "--config",
            str(CONFIG),
            "--no-banner",
            "--log-level",
            "error",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        input="\n".join(lines) + "\n",
        text=True,
        check=False,
        cwd=tmp_path,
    )
    found = {}
    for finding in json.loads(report.read_text() or "[]"):
        found.setdefault(finding["StartLine"], set()).add(finding["RuleID"])
    return found


PRIVATE = [
    ("speaker = 192.168.178.23", "private-ipv4"),
    ("SONOS_IP=10.20.30.40", "private-ipv4"),
    ("nas: 172.20.0.5", "private-ipv4"),
    ("vpn 100.101.102.103", "private-ipv4"),
    ("contact jane.doe@private-mail.de", "email-address"),
    ("mac a4:5e:60:12:34:56", "mac-address"),
    ("mac A4-5E-60-12-34-56", "mac-address"),
    ("uid RINCON_A45E6012345601400", "sonos-player-id"),
    ("household Sonos_AbCdEfGhIjKlMnOpQrStUvWx.AbCdEf", "sonos-household-id"),
    ("ADMIN_PIN=4711", "admin-pin"),
    ('      ADMIN_PIN: "4711"', "admin-pin"),
    ("Session: https://claude.ai/code/session_01AbCdEfGhIjKlMn", "claude-session-link"),
]

ALLOWED = [
    "SONOS_IP=192.0.2.10",
    "speaker 198.51.100.7 and 203.0.113.9",
    "public resolver 8.8.8.8, not private 172.32.0.1",
    "Co-Authored-By: someone <onewaypub@users.noreply.github.com>",
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "Signed-off-by: dependabot[bot] <support@github.com>",
    "mail parent@example.com",
    "mac 00:00:5E:00:53:01",
    "uid RINCON_00005E00530101400 or RINCON_00000000000001400",
    "SA_RINCON52231_X_#Svc52231-0-Token",
    "ADMIN_PIN=change-me",
    "ADMIN_PIN: ${ADMIN_PIN}",
    "ADMIN_PIN=<pin>",
    'admin_pin = get("ADMIN_PIN")',
    "| `ADMIN_PIN` | - | PIN for the parents' page. |",
]


def test_private_data_is_found(tmp_path):
    found = findings([text for text, _ in PRIVATE], tmp_path)
    missed = [
        (text, rule)
        for number, (text, rule) in enumerate(PRIVATE, start=1)
        if rule not in found.get(number, set())
    ]
    assert not missed


def test_placeholders_and_code_are_not_flagged(tmp_path):
    found = findings(ALLOWED, tmp_path)
    assert {ALLOWED[number - 1]: rules for number, rules in found.items()} == {}
