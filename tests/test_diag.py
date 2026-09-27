# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
import io

import pytest

from muckebox import diag
from muckebox.sonos.fake import FakeSonos

ENV = {"SONOS_IP": "192.0.2.10", "MUCKEBOX_FAKE_SONOS": "1"}


def run(*argv, fake=None, monkeypatch=None):
    if fake is not None:
        monkeypatch.setattr(diag, "make_backend", lambda environ: fake)
    out = io.StringIO()
    code = diag.main(list(argv), ENV, out)
    return code, out.getvalue()


def test_status():
    code, out = run("status")
    assert code == 0
    assert "Room:         Kinderzimmer" in out
    assert "Volume:       10" in out


def test_favorites_show_route_and_reason():
    code, out = run("favorites")
    assert code == 0
    assert "direct" in out and "queue" in out
    assert "unsupported (tv_input)" in out
    assert "6 favorites" in out


def test_play(monkeypatch):
    fake = FakeSonos()
    code, out = run("play", "FV:2/3", fake=fake, monkeypatch=monkeypatch)
    assert code == 0
    assert "via direct" in out
    assert fake.state == "playing"


def test_play_unknown_favorite():
    with pytest.raises(SystemExit):
        run("play", "FV:2/99")


def test_errors_are_explained(monkeypatch):
    fake = FakeSonos(reachable=False)
    code, out = run("status", fake=fake, monkeypatch=monkeypatch)
    assert code == 1
    assert "sonos_unreachable" in out
    assert "nicht erreichbar" in out


def test_watch_volume_reports_corrections():
    fake = FakeSonos(volume=80)
    out = io.StringIO()
    args = argparse.Namespace(seconds=1, max=25)
    diag.cmd_watch_volume(fake, out, args, sleep=lambda s: None)
    assert "corrected to 25" in out.getvalue()
    assert fake.volume == 25


def test_invalid_configuration_stops():
    with pytest.raises(SystemExit, match="Configuration error"):
        diag.make_backend({})
