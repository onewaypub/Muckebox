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
# REUSE-IgnoreStart
SPDX_LICENSE = "SPDX-License-Identifier: AGPL-3.0-or-later"
SPDX_COPYRIGHT = "SPDX-FileCopyrightText: 2026 Muckebox contributors"
# REUSE-IgnoreEnd
HEADER_LINES = 10

# License texts are exempt (LICENSES/ is the REUSE location, LICENSE is kept
# for GitHub's license detection).
EXEMPT = {"LICENSE"}  # and the licence texts in LICENSES/ (REUSE: no header there)


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
        if path in EXEMPT or path.startswith("LICENSES/") or reuse_annotated(path):
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


def test_license_copies_are_identical():
    assert (ROOT / "LICENSE").read_bytes() == (ROOT / "LICENSES/AGPL-3.0-or-later.txt").read_bytes()


def test_version_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == muckebox.__version__


def test_tcp_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="must not open network connections"):
        socket.create_connection(("192.0.2.10", 1400), timeout=0.1)


def test_udp_multicast_is_blocked_in_tests():
    # SSDP discovery (what SoCo uses to find speakers) must never leave the test.
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(RuntimeError, match="must not open network connections"),
    ):
        sock.sendto(b"M-SEARCH * HTTP/1.1\r\n\r\n", ("239.255.255.250", 1900))


def test_dns_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="must not resolve host names"):
        socket.getaddrinfo("sonos.example.com", 1400)


def test_loopback_stays_available_in_tests():
    assert socket.getaddrinfo("localhost", 80)


def test_import_rules():
    """soco only inside muckebox.sonos; muckebox.sonos never imports Flask."""
    import_re = re.compile(r"^\s*(?:from|import)\s+([a-z_][\w.]*)", re.MULTILINE)
    offenders = []
    for path in (ROOT / "muckebox").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        in_sonos = relative.startswith("muckebox/sonos/")
        for module in import_re.findall(path.read_text(encoding="utf-8")):
            top = module.split(".")[0]
            if (top == "soco" and not in_sonos) or (top in ("flask", "werkzeug") and in_sonos):
                offenders.append(f"{relative}: {module}")
    assert not offenders
