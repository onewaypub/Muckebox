# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Diagnostics from the command line, e.g. inside the container::

    docker exec -it muckebox python -m muckebox.diag rooms
    docker exec -it muckebox python -m muckebox.diag status
    docker exec -it muckebox python -m muckebox.diag favorites
    docker exec -it muckebox python -m muckebox.diag play FV:2/3
    docker exec -it muckebox python -m muckebox.diag watch-volume

It controls the room chosen on the parents' page (read from settings.json in
DATA_DIR, which it never changes). --room and --ip override it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO

from muckebox.config import data_dir_from
from muckebox.i18n import translate
from muckebox.runtime.clock import SystemClock
from muckebox.runtime.volume_guard import VolumeGuard
from muckebox.settings import DEFAULT_MAX_VOLUME, SettingsFileError, SettingsStore
from muckebox.sonos.backend import SonosBackend
from muckebox.sonos.errors import SonosError
from muckebox.sonos.model import RoomChoice


@dataclass(frozen=True)
class Target:
    """The room to diagnose: from settings.json, overridden by --room/--ip."""

    room: str | None
    room_uid: str | None
    seed_ip: str | None
    max_volume: int
    fake: bool
    #: Why settings.json could not be used, if it exists but cannot be read.
    problem: str | None = None


def load_target(
    environ: Mapping[str, str], room: str | None = None, ip: str | None = None
) -> Target:
    problem = None
    data_dir = data_dir_from(environ)
    try:
        stored = SettingsStore(data_dir, create=False).current()
    except SettingsFileError as exc:
        stored = None  # nothing set up yet (or unreadable): only overrides count
        if (data_dir / "settings.json").exists():
            problem = str(exc)
    same_room = stored is not None and room in (None, stored.room)
    return Target(
        room=room or (stored.room if stored else None),
        room_uid=stored.room_uid if same_room and stored else None,
        seed_ip=ip or (stored.seed_ip if stored else None),
        max_volume=stored.max_volume if stored else DEFAULT_MAX_VOLUME,
        fake=environ.get("MUCKEBOX_FAKE_SONOS") == "1",
        problem=problem,
    )


def make_backend(target: Target) -> SonosBackend:
    if target.room is None:
        raise SystemExit(
            "No room chosen yet. Choose one on the parents' page or pass --room "
            "(the 'rooms' command lists them)."
        )
    if target.fake:
        from muckebox.sonos.fake import FakeHousehold

        return FakeHousehold().backend(target.room)
    from muckebox.sonos.soco_backend import SocoBackend, configure_soco

    configure_soco()
    return SocoBackend(room=target.room, room_uid=target.room_uid, seed_ip=target.seed_ip)


def find_rooms(target: Target) -> list[RoomChoice]:
    if target.fake:
        from muckebox.sonos.fake import FakeHousehold

        return FakeHousehold().find_rooms(target.seed_ip)
    from muckebox.sonos.soco_backend import configure_soco
    from muckebox.sonos.soco_backend import find_rooms as soco_find_rooms

    configure_soco()
    return soco_find_rooms(target.seed_ip)


def cmd_rooms(target: Target, out: TextIO) -> None:
    started = time.monotonic()
    rooms = find_rooms(target)
    out.write(f"{'ROOM':<24} {'IP':<16} NOTE\n")
    for room in rooms:
        notes = []
        if room.name == target.room:
            notes.append("chosen")
        if room.grouped:
            notes.append("grouped")
        out.write(f"{room.name:<24} {room.ip:<16} {', '.join(notes)}\n")
    out.write(f"{len(rooms)} rooms ({time.monotonic() - started:.1f} s)\n")


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
    limit = args.limit if args.max is None else args.max
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
    parser.add_argument("--room", help="room name (default: the room chosen on the parents' page)")
    parser.add_argument("--ip", help="IP address of any speaker (default: from the settings)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("rooms", help="list the rooms Muckebox can find")
    sub.add_parser("status", help="find the room and show its state")
    sub.add_parser("favorites", help="list the Sonos favorites and how each would play")
    play = sub.add_parser("play", help="play a favorite by its ID (see 'favorites')")
    play.add_argument("item_id")
    watch = sub.add_parser("watch-volume", help="run the volume guard and report corrections")
    watch.add_argument("--seconds", type=int, default=60)
    watch.add_argument("--max", type=int, default=None, help="limit (default: the saved limit)")
    args = parser.parse_args(argv)

    target = load_target(environ, args.room, args.ip)
    if target.problem:
        out.write(f"Note: the saved settings are not used: {target.problem}\n")
    try:
        if args.command == "rooms":
            cmd_rooms(target, out)
        else:
            args.limit = target.max_volume
            COMMANDS[args.command](make_backend(target), out, args)
    except SonosError as exc:
        out.write(f"Error: {exc.code}: {translate('error.' + exc.code)}\n")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
