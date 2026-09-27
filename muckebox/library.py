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
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from muckebox.sonos.model import FavoriteRef, Route, ShareLinkRef
from muckebox.storage import atomic_write

log = logging.getLogger(__name__)

SCHEMA = 1
MAX_TITLE_LENGTH = 60


class LibraryError(Exception):
    code = "library_error"


class LibraryFileError(Exception):
    """library.json was written by a newer Muckebox; it is left untouched."""


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


# Control characters, lone surrogates (half an emoji from a truncated
# JSON string) and non-characters: never stored in titles.
_UNSAFE_TITLE_RE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")


def clean_title(title: str) -> str:
    cleaned = " ".join(_UNSAFE_TITLE_RE.sub(" ", str(title)).split())
    if not cleaned or len(cleaned) > MAX_TITLE_LENGTH:
        raise InvalidTitle(f"title must have 1 to {MAX_TITLE_LENGTH} characters")
    return cleaned


class Library:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._rev = 0
        self._tiles: list[Tile] = []
        #: Set when library.json could not be read and was moved aside:
        #: {"code": "library_corrupt", "file": <name of the moved file>}.
        self.load_problem: dict[str, str] | None = None
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
            self._commit(lambda: self._tiles.remove(tile))
            return Tile(**asdict(tile))

    def rename(self, tile_id: str, title: str, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            old, tile.title = tile.title, clean_title(title)
            self._commit(lambda: setattr(tile, "title", old))
            return Tile(**asdict(tile))

    def move(self, tile_id: str, direction: str, expected_rev: int | None = None) -> None:
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        with self._lock:
            self._check_rev(expected_rev)
            index = self._tiles.index(self._find(tile_id))
            target = index - 1 if direction == "up" else index + 1
            if 0 <= target < len(self._tiles):
                tiles = self._tiles

                def swap() -> None:
                    tiles[index], tiles[target] = tiles[target], tiles[index]

                swap()
                self._commit(swap)

    def set_cover(self, tile_id: str, cover: str | None, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            old, tile.cover = tile.cover, cover
            self._commit(lambda: setattr(tile, "cover", old))
            return Tile(**asdict(tile))

    def remove(self, tile_id: str, expected_rev: int | None = None) -> Tile:
        with self._lock:
            self._check_rev(expected_rev)
            tile = self._find(tile_id)
            index = self._tiles.index(tile)
            self._tiles.remove(tile)
            self._commit(lambda: self._tiles.insert(index, tile))
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
            schema = data.get("schema") if isinstance(data, dict) else None
            if type(schema) is int and schema > SCHEMA:
                raise LibraryFileError(
                    f"{self.path} was written by a newer Muckebox (schema {schema}); "
                    "update Muckebox or restore a backup"
                )
            if schema != SCHEMA:
                raise ValueError(f"unsupported schema {schema!r}")
            # Fields added by later versions are ignored instead of making the
            # whole library unreadable.
            tiles = [
                Tile(**{key: value for key, value in tile.items() if key in _TILE_FIELDS})
                for tile in data["tiles"]
            ]
            rev = int(data["rev"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            broken = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            shutil.move(self.path, broken)
            self.load_problem = {"code": "library_corrupt", "file": broken.name}
            log.error(
                "Library: %s could not be read (%s); moved to %s. Starting with an empty library.",
                self.path.name,
                exc,
                broken,
            )
            return
        self._tiles, self._rev = tiles, rev

    def _save(self) -> None:
        """Write the library; advance the revision only once the file is in place."""
        data = {
            "schema": SCHEMA,
            "rev": self._rev + 1,
            "updated_at": _now(),
            "tiles": [asdict(tile) for tile in self._tiles],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write(self.path, payload, backup=True)
        self._rev += 1

    def _commit(self, undo: Callable[[], None]) -> None:
        """Save; if that fails, undo the in-memory change and re-raise."""
        try:
            self._save()
        except BaseException:
            undo()
            raise


_TILE_FIELDS = frozenset(Tile.__dataclass_fields__)


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
