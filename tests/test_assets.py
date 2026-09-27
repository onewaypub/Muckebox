# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The games' sounds and pictures: files, credits, licences and names match."""

import re
import tomllib
from pathlib import Path

from muckebox import assets
from muckebox.i18n import CATALOGUES, DEFAULT_LANG

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "muckebox" / "static"
CATALOGUE = (STATIC / "js" / "catalogue.js").read_text(encoding="utf-8")
TEXTS = CATALOGUES[DEFAULT_LANG]


def js_list(name):
    block = re.search(rf"export const {name} = \[(.*?)\];", CATALOGUE, re.S).group(1)
    return (
        re.findall(r'"([a-z_]+)"', block)
        if name != "MOVES"
        else re.findall(r'id: "([a-z_]+)"', block)
    )


def test_every_sound_file_is_credited_and_every_credit_has_a_file():
    files = {path.stem for path in (STATIC / "sounds").glob("*.mp3")} - {"silence"}
    assert files == set(assets.SOUNDS)


def test_every_picture_is_listed():
    assert {path.stem for path in (STATIC / "pictures").glob("*.svg")} == set(assets.PICTURES)


def test_licences_have_texts_and_match_reuse():
    config = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    by_path = {
        annotation["path"]: annotation["SPDX-License-Identifier"]
        for annotation in config["annotations"]
        if isinstance(annotation["path"], str)
    }
    for key, sound in assets.SOUNDS.items():
        assert sound.license in assets.LICENCES
        assert (ROOT / "LICENSES" / f"{sound.license}.txt").is_file()
        assert by_path[f"muckebox/static/sounds/{key}.mp3"] == sound.license
        assert sound.source.startswith("https://commons.wikimedia.org/wiki/File:")
    assert by_path["muckebox/static/pictures/*.svg"] == assets.TWEMOJI_LICENSE


def test_assets_stay_small():
    sounds = sum(path.stat().st_size for path in (STATIC / "sounds").glob("*.mp3"))
    pictures = sum(path.stat().st_size for path in (STATIC / "pictures").glob("*.svg"))
    assert sounds < 1_000_000 and pictures < 300_000


def test_pictures_are_plain_svg():
    for path in (STATIC / "pictures").glob("*.svg"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("<svg") and "<script" not in text and "href" not in text


def test_the_game_catalogue_has_files_and_names():
    for sound in js_list("ANIMALS") + js_list("EVERYDAY"):
        assert (STATIC / "sounds" / f"{sound}.mp3").is_file()
        assert (STATIC / "pictures" / f"{sound}.svg").is_file()
        assert f"sound.{sound}" in TEXTS
    for move in js_list("MOVES"):
        assert (STATIC / "pictures" / f"{move}.svg").is_file()
        assert f"move.{move}" in TEXTS
    for game in ("freeze_dance", "sound_quiz", "move_like", "breathing"):
        assert (STATIC / "pictures" / f"{game}.svg").is_file()


def test_credits_for_the_parents_page():
    items = assets.credits()
    assert len(items) == len(assets.SOUNDS) + 1
    assert all(item["license_url"].startswith("https://") for item in items)


def test_reuse_annotations_name_existing_files():
    config = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    for annotation in config["annotations"]:
        paths = annotation["path"] if isinstance(annotation["path"], list) else [annotation["path"]]
        for path in paths:
            assert list(ROOT.glob(path)), f"REUSE.toml names a missing file: {path}"
    used = {sound.license for sound in assets.SOUNDS.values()} | {assets.TWEMOJI_LICENSE, "CC0-1.0"}
    texts = {path.stem for path in (ROOT / "LICENSES").glob("*.txt")}
    assert texts - {"AGPL-3.0-or-later"} == used  # no licence text without a file using it
