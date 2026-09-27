# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "privacy_scan.py"
spec = importlib.util.spec_from_file_location("privacy_scan", SCRIPT)
privacy_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy_scan)


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("onewaypub", "onewaypub@users.noreply.github.com"),
        ("someone", "12345+someone@users.noreply.github.com"),
        ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
        ("GitHub", "noreply@github.com"),
        ("Claude", "noreply@anthropic.com"),
    ],
)
def test_allowed_identities(name, email):
    assert privacy_scan.identity_problem(name, email) is None


@pytest.mark.parametrize(
    ("name", "email", "reason"),
    [
        ("Jane Doe", "jane@example.com", "non-noreply email address"),
        ("Jane Doe", "12345+someone@users.noreply.github.com", "differs from the GitHub login"),
        ("Jane Doe", "noreply@github.com", "unexpected name"),
    ],
)
def test_rejected_identities(name, email, reason):
    assert reason in privacy_scan.identity_problem(name, email)


def test_problem_messages_do_not_repeat_private_parts():
    problem = privacy_scan.identity_problem("Jane Doe", "jane.doe@example.com")
    assert "jane" not in problem.lower()
