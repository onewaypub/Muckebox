# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Diagnostics from the command line, e.g. inside the container::

    docker exec -it muckebox python -m muckebox.diag status
    docker exec -it muckebox python -m muckebox.diag favorites
    docker exec -it muckebox python -m muckebox.diag play FV:2/3
    docker exec -it muckebox python -m muckebox.diag watch-volume

It uses the same environment variables as Muckebox itself.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

from muckebox.config import load_settings
from muckebox.i18n import translate
from muckebox.runtime.clock import SystemClock
from muckebox.runtime.volume_guard import VolumeGuard
from muckebox.sonos.backend import SonosBackend
from muckebox.sonos.errors import SonosError


def make_backend(environ: Mapping[str, str]) -> SonosBackend:
    settings = load_settings(environ)
    if environ.get("MUCKEBOX_FAKE_SONOS") == "1":
        from muckebox.sonos.fake import FakeSonos

        return FakeSonos()
    from muckebox.sonos.soco_backend import SocoBackend, configure_soco

    configure_soco()
    if not settings.sonos_config_ok:
        problems = ", ".join(p.code for p in settings.problems if p.severity == "error")
        raise SystemExit(f"Configuration error: {problems}")
    return SocoBackend(room=settings.sonos_room, seed_ip=settings.sonos_ip)


def cmd_status(backend: SonosBackend, out: TextIO, args: argparse.Namespace) -> None:
    started = time.monotonic()
    room = backend.resolve()
    out.write(f"Room:         {room.name} ({time.monotonic() - started:.1f} s to resolve)\n")
    out.write(f"Player IP:    {room.player_ip}\n")
    out.write(f"Coordinator:  {room.coordinator_ip}{' (grouped)' if room.grouped else ''}\n")
    out.write(f"Volume:       {backend.get_volume()}\n")
    out.write(f"Fixed volume: {'yes' if backend.fixed_volume() else 'no'}\n")
    playback = backend.playback()
    out.write(f"Playback:     {playback.state}\n")
    out.write(f"Media URI:    {playback.media_uri or '-'}\n")
    out.write(f"Actions:      {', '.join(sorted(playback.actions)) or '-'}\n")


def cmd_favorites(backend: SonosBackend, out: TextIO, args: argparse.Namespace) -> None:
    backend.resolve()
    favorites = backend.list_favorites()
    out.write(f"{'ID':<10} {'ROUTE':<24} {'SOURCE':<22} TITLE\n")
    for favorite in favorites:
        route = favorite.route.value
        if favorite.reason:
            route += f" ({favorite.reason})"
        scheme = favorite.ref.uri.split(":", 1)[0] if favorite.ref else "-"
        out.write(f"{favorite.item_id:<10} {route:<24} {scheme:<22} {favorite.title}")
        out.write(f"  [{favorite.description}]\n" if favorite.description else "\n")
    out.write(f"{len(favorites)} favorites\n")


def cmd_play(backend: SonosBackend, out: TextIO, args: argparse.Namespace) -> None:
    backend.resolve()
    favorite = next((f for f in backend.list_favorites() if f.item_id == args.item_id), None)
    if favorite is None or favorite.ref is None:
        raise SystemExit(f"Favorite {args.item_id} not found")
    started = time.monotonic()
    route = backend.play_favorite(favorite.ref, favorite.route)
    out.write(
        f"Started {favorite.title!r} via {route.value} in {time.monotonic() - started:.1f} s\n"
    )
    out.write(f"Playback: {backend.playback().state}\n")


def cmd_watch_volume(
    backend: SonosBackend,
    out: TextIO,
    args: argparse.Namespace,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    limit = load_settings(os.environ).max_volume if args.max is None else args.max
    backend.resolve()
    guard = VolumeGuard(backend, lambda: limit, SystemClock())
    out.write(f"Watching the volume (limit {limit}) for {args.seconds} s; Ctrl+C to stop.\n")
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        before = guard.corrections
        volume = guard.step()
        if guard.corrections != before:
            out.write(f"{time.strftime('%H:%M:%S')} corrected to {volume}\n")
        sleep(1.0)
    out.write(f"{guard.corrections} corrections\n")


COMMANDS = {
    "status": cmd_status,
    "favorites": cmd_favorites,
    "play": cmd_play,
    "watch-volume": cmd_watch_volume,
}


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m muckebox.diag",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="find the room and show its state")
    sub.add_parser("favorites", help="list the Sonos favorites and how each would play")
    play = sub.add_parser("play", help="play a favorite by its ID (see 'favorites')")
    play.add_argument("item_id")
    watch = sub.add_parser("watch-volume", help="run the volume guard and report corrections")
    watch.add_argument("--seconds", type=int, default=60)
    watch.add_argument("--max", type=int, default=None, help="limit (default: MAX_VOLUME)")
    args = parser.parse_args(argv)

    backend = make_backend(environ)
    try:
        COMMANDS[args.command](backend, out, args)
    except SonosError as exc:
        out.write(f"Error: {exc.code}: {translate('error.' + exc.code)}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
