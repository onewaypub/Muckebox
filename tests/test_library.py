# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import json

import pytest

from muckebox.library import (
    InvalidTitle,
    Library,
    RevConflict,
    TileNotFound,
    favorite_source,
    sharelink_source,
)
from muckebox.sonos.model import FavoriteRef, Route, ShareLinkRef

REF = FavoriteRef(
    "x-sonosapi-stream:s1?sid=254",
    "x-sonosapi-stream:*:*:*",
    "<DIDL/>",
    "object.item.audioItem.audioBroadcast",
    "Kinderradio",
)


@pytest.fixture
def library(tmp_path):
    return Library(tmp_path / "library.json")


def add(library, title="Radio"):
    return library.add(title, favorite_source("FV:2/1", REF, Route.DIRECT, "TuneIn"))


def test_starts_empty(library):
    assert library.tiles() == []
    assert library.rev == 0


def test_add_persists_and_survives_reload(library, tmp_path):
    tile = add(library, "  Kinder   Radio ")
    assert tile.title == "Kinder Radio"
    assert tile.id.startswith("t")
    reloaded = Library(tmp_path / "library.json")
    assert [t.title for t in reloaded.tiles()] == ["Kinder Radio"]
    assert reloaded.rev == 1
    restored = reloaded.get(tile.id)
    assert restored.favorite_ref() == REF
    assert restored.favorite_route() is Route.DIRECT


def test_share_link_tile(library):
    link = ShareLinkRef("apple_music", "album", "1440857781")
    tile = library.add("Album", sharelink_source(link, "https://music.apple.com/x"))
    assert library.get(tile.id).share_link() == link


def test_rename_move_remove(library):
    a, b, c = add(library, "A"), add(library, "B"), add(library, "C")
    library.rename(b.id, "Bee")
    library.move(c.id, "up")
    assert [t.title for t in library.tiles()] == ["A", "C", "Bee"]
    library.move(a.id, "up")  # already first: no change
    library.move(b.id, "down")  # already last: no change
    assert [t.title for t in library.tiles()] == ["A", "C", "Bee"]
    removed = library.remove(a.id)
    assert removed.title == "A"
    assert [t.title for t in library.tiles()] == ["C", "Bee"]


def test_invalid_move_direction(library):
    tile = add(library)
    with pytest.raises(ValueError):
        library.move(tile.id, "sideways")


@pytest.mark.parametrize("title", ["", "   ", "x" * 61])
def test_invalid_titles(library, title):
    with pytest.raises(InvalidTitle):
        add(library, title)


def test_unknown_tile(library):
    with pytest.raises(TileNotFound):
        library.get("t-missing")
    with pytest.raises(TileNotFound):
        library.remove("t-missing")


def test_stale_revision_is_rejected(library):
    tile = add(library)
    with pytest.raises(RevConflict):
        library.rename(tile.id, "New", expected_rev=0)
    library.rename(tile.id, "New", expected_rev=1)
    assert library.rev == 2


def test_returned_tiles_are_copies(library):
    tile = add(library)
    tile.title = "changed outside"
    assert library.get(tile.id).title == "Radio"


def test_writes_keep_a_backup(library, tmp_path):
    add(library, "A")
    add(library, "B")
    backup = json.loads((tmp_path / "library.json.bak").read_text())
    assert [t["title"] for t in backup["tiles"]] == ["A"]
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_file_is_moved_aside(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{not json")
    library = Library(path)
    assert library.tiles() == []
    assert library.load_problem["code"] == "library_corrupt"
    assert library.load_problem["file"].startswith("library.json.corrupt-")
    assert list(tmp_path.glob("library.json.corrupt-*"))
    add(library)  # a new, valid file can be written
    assert Library(path).rev == 1


def test_unknown_schema_is_not_loaded(tmp_path):
    path = tmp_path / "library.json"
    path.write_text(json.dumps({"schema": "one", "rev": 3, "tiles": []}))
    assert Library(path).load_problem


def test_covers_in_use(library):
    tile = add(library)
    library.set_cover(tile.id, "0123456789abcdef0123.jpg")
    assert library.covers_in_use() == {"0123456789abcdef0123.jpg"}


def test_titles_lose_control_characters_and_broken_emoji(library):
    tile = add(library, "Bibi \ud83d Tina\x07")
    assert tile.title == "Bibi Tina"


@pytest.mark.parametrize(
    "change",
    [
        lambda lib, tile: lib.add("New", tile.source),
        lambda lib, tile: lib.rename(tile.id, "Renamed"),
        lambda lib, tile: lib.move(tile.id, "down"),
        lambda lib, tile: lib.set_cover(tile.id, "0123456789abcdef0123.jpg"),
        lambda lib, tile: lib.remove(tile.id),
    ],
    ids=["add", "rename", "move", "cover", "remove"],
)
def test_a_failed_write_changes_nothing(library, monkeypatch, change):
    tile = add(library, "A")
    add(library, "B")
    before = [(t.id, t.title, t.cover) for t in library.tiles()]
    rev = library.rev

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("muckebox.storage.os.fsync", disk_full)
    with pytest.raises(OSError):
        change(library, tile)
    assert [(t.id, t.title, t.cover) for t in library.tiles()] == before
    assert library.rev == rev
    monkeypatch.undo()
    library.rename(tile.id, "Works again")
    assert library.rev == rev + 1


def test_fields_of_a_newer_version_are_ignored(tmp_path):
    path = tmp_path / "library.json"
    Library(path).add("Radio", {"type": "favorite", "uri": "x-sonosapi-stream:s1"})
    data = json.loads(path.read_text())
    data["tiles"][0]["colour"] = "red"  # a field some later version might add
    path.write_text(json.dumps(data))
    library = Library(path)
    assert library.load_problem is None
    assert [tile.title for tile in library.tiles()] == ["Radio"]


def test_a_library_from_a_newer_version_is_left_alone(tmp_path):
    from muckebox.library import LibraryFileError

    path = tmp_path / "library.json"
    path.write_text(json.dumps({"schema": 2, "rev": 1, "tiles": []}))
    with pytest.raises(LibraryFileError, match="newer"):
        Library(path)
    assert json.loads(path.read_text())["schema"] == 2
    assert not list(tmp_path.glob("library.json.corrupt-*"))
