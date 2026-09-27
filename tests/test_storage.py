# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import stat

import pytest

from muckebox.storage import atomic_write


def test_writes_and_replaces(tmp_path):
    path = tmp_path / "sub" / "file.json"
    atomic_write(path, b"one")
    atomic_write(path, b"two")
    assert path.read_bytes() == b"two"
    assert not (tmp_path / "sub" / "file.json.bak").exists()
    assert not list((tmp_path / "sub").glob("*.tmp"))


def test_backup_keeps_the_previous_version(tmp_path):
    path = tmp_path / "file.json"
    atomic_write(path, b"one", backup=True)
    atomic_write(path, b"two", backup=True)
    assert (tmp_path / "file.json.bak").read_bytes() == b"one"


def test_private_mode_from_the_start(tmp_path):
    path = tmp_path / "secret.json"
    atomic_write(path, b"x", mode=0o600)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "file.json"
    atomic_write(path, b"old")

    def disk_full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("muckebox.storage.os.fsync", disk_full)
    with pytest.raises(OSError):
        atomic_write(path, b"new")
    assert path.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))
