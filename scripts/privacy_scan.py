#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan the repository for secrets and private data before publishing.

Checks, all with the rules in .gitleaks.toml:

1. the full git history of all local branches,
2. the working tree (tracked and untracked files that git does not ignore),
3. commit messages,
4. author and committer email addresses (must be noreply addresses).

Exit code 0 means nothing was found.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".gitleaks.toml"
ALLOWED_COMMIT_EMAIL = re.compile(
    r"(@users\.noreply\.github\.com|^noreply@(anthropic|github)\.com)$", re.IGNORECASE
)


def find_gitleaks() -> str:
    local = ROOT / ".tools" / "bin" / "gitleaks"
    if local.is_file():
        return str(local)
    found = shutil.which("gitleaks")
    if found:
        return found
    sys.exit("gitleaks not found; run scripts/install-gitleaks first")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def gitleaks(binary: str, *args: str, stdin: str | None = None, cwd: Path = ROOT) -> bool:
    command = [binary, *args, "--config", str(CONFIG), "--no-banner", "--redact", "-v"]
    command += ["--log-level", "warn"]
    result = subprocess.run(command, cwd=cwd, input=stdin, text=True, check=False)
    return result.returncode == 0


def scan_history(binary: str) -> bool:
    print("• git history (all refs)")
    return gitleaks(binary, "git", "--log-opts=--all", ".")


def scan_working_tree(binary: str) -> bool:
    print("• working tree")
    files = [p for p in git("ls-files", "-co", "--exclude-standard", "-z").split("\0") if p]
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "tree.tar"
        with tarfile.open(archive, "w") as tar:
            for name in files:
                if (ROOT / name).is_file():
                    tar.add(ROOT / name, arcname=name)
        snapshot = Path(tmp) / "tree"
        with tarfile.open(archive) as tar:
            tar.extractall(snapshot, filter="data")
        return gitleaks(binary, "dir", ".", cwd=snapshot)


def scan_commit_messages(binary: str) -> bool:
    print("• commit messages")
    return gitleaks(binary, "stdin", stdin=git("log", "--all", "--format=%B"))


def check_commit_emails() -> bool:
    print("• commit author and committer emails")
    emails = set(git("log", "--all", "--format=%ae%n%ce").split())
    bad = sorted(e for e in emails if not ALLOWED_COMMIT_EMAIL.search(e))
    for email in bad:
        # Show only the domain; the local part may itself be private.
        print(f"  commit metadata contains a non-noreply address at @{email.split('@')[-1]}")
    return not bad


def main() -> int:
    os.chdir(ROOT)
    binary = find_gitleaks()
    results = [
        scan_history(binary),
        scan_working_tree(binary),
        scan_commit_messages(binary),
        check_commit_emails(),
    ]
    if all(results):
        print("Privacy scan: no findings.")
        return 0
    print("Privacy scan: FINDINGS - do not publish before fixing them.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
