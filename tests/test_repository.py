# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository hygiene: license headers, dependency documentation, version."""

import re
import socket
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import pytest

import muckebox

ROOT = Path(__file__).resolve().parent.parent
SPDX_LICENSE = "SPDX-License-Identifier: AGPL-3.0-or-later"
SPDX_COPYRIGHT = "SPDX-FileCopyrightText: 2026 Muckebox contributors"
HEADER_LINES = 10

# The license text itself is the only file exempt from REUSE annotations.
EXEMPT = {"LICENSE"}


def repository_files():
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(p for p in output.split("\0") if p and (ROOT / p).is_file())


def reuse_annotated(path):
    config = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    patterns = [
        pattern
        for annotation in config.get("annotations", [])
        for pattern in (
            annotation["path"] if isinstance(annotation["path"], list) else [annotation["path"]]
        )
    ]
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def test_every_file_has_an_spdx_header_or_reuse_annotation():
    missing = []
    for path in repository_files():
        if path in EXEMPT or reuse_annotated(path):
            continue
        try:
            head = (ROOT / path).read_text(encoding="utf-8").splitlines()[:HEADER_LINES]
        except UnicodeDecodeError:
            missing.append(f"{path} (binary; add it to REUSE.toml)")
            continue
        text = "\n".join(head)
        if SPDX_LICENSE not in text or SPDX_COPYRIGHT not in text:
            missing.append(path)
    assert not missing, "Files without SPDX header:\n" + "\n".join(missing)


def normalise(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def test_readme_lists_exactly_the_runtime_dependencies():
    locked = {
        normalise(m.group(1))
        for m in re.finditer(
            r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==",
            (ROOT / "requirements.txt").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Dependencies and licenses", 1)[1].split("\n## ", 1)[0]
    documented = {normalise(m.group(1)) for m in re.finditer(r"^\| \[([^\]]+)\]", section, re.M)}
    assert locked, "requirements.txt contains no pinned packages"
    assert documented == locked


def test_version_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == muckebox.__version__


def test_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="must not open network connections"):
        socket.create_connection(("192.0.2.10", 1400), timeout=0.1)
