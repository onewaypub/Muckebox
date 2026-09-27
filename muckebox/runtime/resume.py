# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where each album tile stopped ("Weiterhören"), kept in ``DATA_DIR/resume.json``.

Positions change every second while something plays. Writing them that
often would keep a NAS disk awake, so the file is written when playback
pauses or stops, when another tile starts, every few minutes while playing,
and when Muckebox stops.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from muckebox.sonos.model import Position, StartAt
from muckebox.storage import atomic_write

log = logging.getLogger(__name__)

#: While playing, the file is written at most this often (seconds).
SAVE_INTERVAL = 300.0
#: A stop within this many seconds of the end of the last track means the
#: album was heard to the end: next time it starts from the beginning.
FINISHED_MARGIN = 30


@dataclass
class Saved:
    track: int
    seconds: int
    duration: int | None
    track_uri: str
    queue_length: int | None


class ResumeStore:
    def __init__(self, path: Path, clock) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._positions: dict[str, Saved] = self._load()
        self._version = 0
        self._saved_version = 0
        self._urgent = False
        self._saved_at = clock.monotonic()
        #: Tiles set back to the beginning ("Von vorn"): not recorded again
        #: until they are started anew.
        self._restarted: set[str] = set()

    def get(self, tile_id: str) -> StartAt | None:
        with self._lock:
            saved = self._positions.get(tile_id)
        if saved is None:
            return None
        return StartAt(track=saved.track, seconds=saved.seconds, track_uri=saved.track_uri)

    def saved(self, tile_id: str) -> Saved | None:
        with self._lock:
            return self._positions.get(tile_id)

    def record(self, tile_id: str, position: Position, queue_length: int | None) -> None:
        saved = Saved(
            position.track, position.seconds, position.duration, position.track_uri, queue_length
        )
        with self._lock:
            if tile_id in self._restarted or self._positions.get(tile_id) == saved:
                return
            self._positions[tile_id] = saved
            self._version += 1

    def stopped(self, tile_id: str) -> None:
        """Playback of the tile stopped: forget it if it had reached its end."""
        with self._lock:
            saved = self._positions.get(tile_id)
            if saved is not None and _finished(saved):
                del self._positions[tile_id]
                self._version += 1
            self._urgent = True

    def urgent(self) -> None:
        """Write soon (paused, or another tile starts)."""
        with self._lock:
            self._urgent = True

    def restart(self, tile_id: str) -> None:
        """ "Von vorn": forget the position and do not record it again until the
        tile is started anew (it may still be loaded, e.g. paused)."""
        with self._lock:
            self._restarted.add(tile_id)
        self.clear(tile_id)

    def started(self, tile_id: str) -> None:
        with self._lock:
            self._restarted.discard(tile_id)

    def clear(self, tile_id: str) -> None:
        with self._lock:
            if self._positions.pop(tile_id, None) is not None:
                self._version += 1
                self._urgent = True

    def prune(self, tile_ids: set[str]) -> None:
        """Drop positions of tiles that no longer exist."""
        with self._lock:
            gone = set(self._positions) - tile_ids
            for tile_id in gone:
                del self._positions[tile_id]
            if gone:
                self._version += 1

    def save(self, force: bool = False) -> None:
        now = self._clock.monotonic()
        with self._lock:
            if self._version == self._saved_version:
                self._urgent = False  # nothing to write: the request is used up
                return
            if not (force or self._urgent or now - self._saved_at >= SAVE_INTERVAL):
                return
            version = self._version
            payload = json.dumps(
                {tile_id: asdict(saved) for tile_id, saved in self._positions.items()}
            ).encode("utf-8")
            self._urgent = False
            self._saved_at = now
        try:
            atomic_write(self.path, payload)
        except OSError as exc:
            log.warning("Could not save %s: %s", self.path.name, exc)
        with self._lock:
            self._saved_version = max(self._saved_version, version)

    def _load(self) -> dict[str, Saved]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {
                str(tile_id): Saved(
                    track=int(entry["track"]),
                    seconds=int(entry["seconds"]),
                    duration=None if entry.get("duration") is None else int(entry["duration"]),
                    track_uri=str(entry["track_uri"]),
                    queue_length=None
                    if entry.get("queue_length") is None
                    else int(entry["queue_length"]),
                )
                for tile_id, entry in data.items()
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Ignoring %s (%s)", self.path.name, exc)
            return {}


def _finished(saved: Saved) -> bool:
    last_track = saved.queue_length is not None and saved.track >= saved.queue_length
    near_end = saved.duration is not None and saved.seconds >= saved.duration - FINISHED_MARGIN
    return last_track and near_end
