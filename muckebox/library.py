# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tile library, stored as ``library.json`` in the data directory.

Every change increases the revision counter ``rev``. Callers that pass the
revision they last saw get :class:`RevConflict` if somebody else changed the
library in between. Writes are atomic: a temporary file is written, flushed
to disk and renamed over the old file, which is kept as ``library.json.bak``.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from muckebox.sonos.model import FavoriteRef, Route, ShareLinkRef

log = logging.getLogger(__name__)

SCHEMA = 1
MAX_TITLE_LENGTH = 60


class LibraryError(Exception):
    code = "library_error"


class TileNotFound(LibraryError):
    code = "tile_not_found"


class RevConflict(LibraryError):
    code = "rev_conflict"


class InvalidTitle(LibraryError):
    code = "title_invalid"


@dataclass
class Tile:
    id: str
    title: str
    source: dict[str, Any]
    cover: str | None = None
    created_at: str = field(default_factory=lambda: _now())

    @property
    def kind(self) -> str:
        return self.source["type"]

    def favorite_ref(self) -> FavoriteRef:
        s = self.source
        return FavoriteRef(
            uri=s["uri"],
            protocol_info=s.get("protocol_info", ""),
            res_md=s.get("res_md", ""),
            item_class=s.get("item_class", ""),
            title=s.get("sonos_title") or self.title,
        )

    def favorite_route(self) -> Route:
        return Route(self.source.get("route", Route.QUEUE))

    def share_link(self) -> ShareLinkRef:
        s = self.source
        return ShareLinkRef(service=s["service"], kind=s["kind"], item_id=s["item_id"])

    def to_public(self, cover_url: str | None) -> dict[str, Any]:
        """The fields the kids view needs."""
        return {"id": self.id, "title": self.title, "cover": cover_url}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_title(title: str) -> str:
    cleaned = " ".join(str(title).split())
    if not cleaned or len(cleaned) > MAX_TITLE_LENGTH:
        raise InvalidTitle(f"title must have 1 to {MAX_TITLE_LENGTH} characters")
    return cleaned


class Library:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._rev = 0
        self._tiles: list[Tile] = []
        #: Set when library.json could not be read and was moved aside.
        self.load_problem: str | None = None
        self._load()

    # -- reading ----------------------------------------------------------

    @property
    def rev(self) -> int:
        return self._rev

    def tiles(self) -> list[Tile]:
        with self._lock:
            return [Tile(**asdict(tile)) for tile in self._tiles]

    def get(self, tile_id: str) -> Tile:
        with self._lock:
            for tile in self._tiles:
                if tile.id == tile_id:
                    return Tile(**asdict(tile))
        raise TileNotFound(tile_id)

    def covers_in_use(self) -> set[str]:
        with self._lock:
            return {tile.cover for tile in self._tiles if tile.cover}

    # -- changing -----------------------------------------------------------

    def add(
        self,
        title: str,
        source: dict[str, Any],
        cover: str | None = None,
        expected_rev: int | None = None,
    ) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = Tile(
                id="t" + secrets.token_hex(8), title=clean_title(title), source=source, cover=cover
            )
            self._tiles.append(tile)
            self._save()
            return Tile(**asdict(tile))

    def rename(self, tile_id: str, title: str, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            tile.title = clean_title(title)
            self._save()
            return Tile(**asdict(tile))

    def move(self, tile_id: str, direction: str, expected_rev: int | None = None) -> None:
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        with self._lock:
            self._check_rev(expected_rev)
            index = self._tiles.index(self._find(tile_id))
            target = index - 1 if direction == "up" else index + 1
            if 0 <= target < len(self._tiles):
                self._tiles[index], self._tiles[target] = self._tiles[target], self._tiles[index]
                self._save()

    def set_cover(self, tile_id: str, cover: str | None, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            tile.cover = cover
            self._save()
            return Tile(**asdict(tile))

    def remove(self, tile_id: str, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            self._tiles.remove(tile)
            self._save()
            return tile

    # -- internals ----------------------------------------------------------

    def _find(self, tile_id: str) -> Tile:
        for tile in self._tiles:
            if tile.id == tile_id:
                return tile
        raise TileNotFound(tile_id)

    def _check_rev(self, expected_rev: int | None) -> None:
        if expected_rev is not None and expected_rev != self._rev:
            raise RevConflict(f"library changed (rev {self._rev}, expected {expected_rev})")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema") != SCHEMA:
                raise ValueError(f"unsupported schema {data.get('schema')!r}")
            tiles = [Tile(**tile) for tile in data["tiles"]]
            rev = int(data["rev"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            broken = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            shutil.move(self.path, broken)
            self.load_problem = (
                f"{self.path.name} could not be read ({exc}); moved to {broken.name}"
            )
            log.error("Library: %s. Starting with an empty library.", self.load_problem)
            return
        self._tiles, self._rev = tiles, rev

    def _save(self) -> None:
        self._rev += 1
        data = {
            "schema": SCHEMA,
            "rev": self._rev,
            "updated_at": _now(),
            "tiles": [asdict(tile) for tile in self._tiles],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        os.replace(tmp, self.path)


def favorite_source(item_id: str, ref: FavoriteRef, route: Route, description: str) -> dict:
    """The ``source`` of a tile created from a Sonos favorite (a snapshot)."""
    return {
        "type": "favorite",
        "item_id": item_id,
        "uri": ref.uri,
        "protocol_info": ref.protocol_info,
        "res_md": ref.res_md,
        "item_class": ref.item_class,
        "sonos_title": ref.title,
        "route": route.value,
        "description": description,
    }


def sharelink_source(link: ShareLinkRef, url: str) -> dict:
    return {
        "type": "sharelink",
        "service": link.service,
        "kind": link.kind,
        "item_id": link.item_id,
        "url": url,
    }
