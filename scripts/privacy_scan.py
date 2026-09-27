#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan the repository for secrets and private data before publishing.

Checks, all with the rules in .gitleaks.toml:

1. the full git history of all local branches,
2. the working tree (tracked and untracked files that git does not ignore),
3. commit messages,
4. author and committer identities: noreply email addresses only, and the
   name must be the GitHub login (git otherwise records the full name of
   the operating system account).

Exit code 0 means nothing was found.

By default all refs are scanned (use this before publishing). CI passes
``--range BASE..HEAD`` to check only the commits a push or pull request
adds, so that history already public cannot keep every later run red.
"""

from __future__ import annotations

import argparse
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
GITHUB_NOREPLY = re.compile(r"^(?:\d+\+)?(?P<login>[^@]+)@users\.noreply\.github\.com$", re.I)
# Service identities that may appear as author or committer, with their names.
SERVICE_IDENTITIES = {
    "noreply@github.com": {"GitHub"},
    "noreply@anthropic.com": {"Claude"},
}


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


def scan_history(binary: str, revs: str) -> bool:
    print(f"• git history ({revs})")
    return gitleaks(binary, "git", f"--log-opts={revs}", ".")


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


def scan_commit_messages(binary: str, revs: str) -> bool:
    print("• commit messages")
    return gitleaks(binary, "stdin", stdin=git("log", revs, "--format=%B"))


def identity_problem(name: str, email: str) -> str | None:
    """Return why a commit identity must not be published, or None if it is fine."""
    match = GITHUB_NOREPLY.match(email)
    if match:
        if name.casefold() != match.group("login").casefold():
            return "name differs from the GitHub login of its noreply address"
        return None
    allowed_names = SERVICE_IDENTITIES.get(email.lower())
    if allowed_names is None:
        return f"non-noreply email address at @{email.rsplit('@', 1)[-1]}"
    if name not in allowed_names:
        return f"unexpected name for {email}"
    return None


def check_commit_identities(revs: str) -> bool:
    print("• commit author and committer names and emails")
    log = git("log", revs, "--format=%an%x00%ae%n%cn%x00%ce")
    pairs = {tuple(line.split("\0", 1)) for line in log.splitlines() if "\0" in line}
    # Never print the identity itself; it may be the private data.
    problems = sorted({p for name, email in pairs if (p := identity_problem(name, email))})
    for problem in problems:
        print(f"  commit metadata: {problem}")
    return not problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--range",
        dest="revs",
        default="--all",
        help="commits to check, e.g. origin/main..HEAD (default: all refs)",
    )
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    binary = find_gitleaks()
    results = [
        scan_history(binary, args.revs),
        scan_working_tree(binary),
        scan_commit_messages(binary, args.revs),
        check_commit_identities(args.revs),
    ]
    if all(results):
        print("Privacy scan: no findings.")
        return 0
    print("Privacy scan: FINDINGS - do not publish before fixing them.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
