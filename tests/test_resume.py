# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.runtime.clock import FakeClock
from muckebox.runtime.resume import ResumeStore
from muckebox.sonos.model import Position


@pytest.mark.parametrize("content", ["{broken", "[]", '{"t1": {"track": "x"}}', '{"t1": 5}'])
def test_a_broken_file_is_ignored(tmp_path, content, caplog):
    (tmp_path / "resume.json").write_text(content)
    assert ResumeStore(tmp_path / "resume.json", FakeClock()).saved("t1") is None
    assert "Ignoring resume.json" in caplog.text


def test_a_failed_write_is_only_logged(tmp_path, caplog):
    (tmp_path / "blocked").write_text("a file, not a folder")
    store = ResumeStore(tmp_path / "blocked" / "resume.json", FakeClock())
    store.record("t1", Position(2, 30, 600, "x"), 3)
    store.save(force=True)
    assert "Could not save resume.json" in caplog.text


def test_clear_forgets_a_tile(tmp_path):
    store = ResumeStore(tmp_path / "resume.json", FakeClock())
    store.record("t1", Position(2, 30, 600, "x"), 3)
    store.clear("t1")
    store.clear("t1")  # twice is fine
    assert store.get("t1") is None
