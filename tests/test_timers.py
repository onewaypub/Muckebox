# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest

from muckebox.runtime.timers import SleepTimer, TimersFile


def test_changes_are_saved_once(tmp_path, monkeypatch):
    from muckebox.runtime import timers as module

    writes = []
    real = module.atomic_write
    monkeypatch.setattr(module, "atomic_write", lambda *a, **k: writes.append(a) or real(*a, **k))
    timers = TimersFile(tmp_path / "timers.json")
    timers.save()
    assert writes == []  # nothing changed yet
    with timers.change() as state:
        state.override = (1.0, 2.0)
        state.sleep = SleepTimer(ends_at=3.0, lock_end=4.0, fade=60.0)
        state.pre_fade = 17
        state.games_date, state.games_seconds, state.game_mute = "2026-09-28", 90.0, True
    timers.save()
    timers.save()
    assert len(writes) == 1
    again = TimersFile(tmp_path / "timers.json").state
    assert again.override == (1.0, 2.0)
    assert again.sleep == SleepTimer(3.0, 4.0, 60.0)
    assert (again.pre_fade, again.games_seconds, again.game_mute) == (17, 90.0, True)


@pytest.mark.parametrize(
    "content",
    ["{broken", "[]", '{"override": [1]}', '{"sleep": {"ends_at": "x"}}', '{"games_seconds": "1"}'],
)
def test_a_broken_file_is_ignored(tmp_path, content, caplog):
    (tmp_path / "timers.json").write_text(content)
    state = TimersFile(tmp_path / "timers.json").state
    assert state.override is None and state.sleep is None
    assert "Ignoring timers.json" in caplog.text


def test_a_failed_write_is_only_logged(tmp_path, caplog):
    timers = TimersFile(tmp_path / "missing" / "dir" / "timers.json")
    (tmp_path / "missing").write_text("a file, not a folder")
    with timers.change() as state:
        state.done_end = 5.0
    timers.save()
    assert "Could not save timers.json" in caplog.text
    assert json.dumps  # the runtime keeps going
