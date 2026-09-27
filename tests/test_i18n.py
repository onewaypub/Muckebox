# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

import pytest

from muckebox.config import MIN_PIN_LENGTH, load_settings
from muckebox.i18n import CATALOGUES, DEFAULT_LANG, placeholders, translate


def test_default_language_exists():
    assert DEFAULT_LANG in CATALOGUES


@pytest.mark.parametrize("lang", sorted(CATALOGUES))
def test_all_texts_are_non_empty_and_keys_are_namespaced(lang):
    for key, text in CATALOGUES[lang].items():
        assert "." in key, key
        assert text.strip(), key


@pytest.mark.parametrize("lang", sorted(CATALOGUES))
def test_all_languages_have_the_same_keys_and_placeholders(lang):
    reference = CATALOGUES[DEFAULT_LANG]
    catalogue = CATALOGUES[lang]
    assert catalogue.keys() == reference.keys()
    for key, text in catalogue.items():
        assert placeholders(text) == placeholders(reference[key]), key


def test_translate_fills_placeholders():
    text = translate("error.admin_pin_too_short", min_length=MIN_PIN_LENGTH)
    assert f"{MIN_PIN_LENGTH} Zeichen" in text
    assert "{" not in text


def test_translate_leaves_unknown_placeholders_and_ignores_extra_params():
    assert "{min_length}" in translate("error.admin_pin_too_short", other=1)


def test_unknown_key_returns_key():
    assert translate("error.does_not_exist") == "error.does_not_exist"


def test_unknown_language_falls_back_to_default():
    assert translate("error.not_found", lang="xx") == translate("error.not_found")


def test_every_config_problem_has_a_text():
    environments = [
        {},
        {"SONOS_IP": "not a host"},
        {"SONOS_IP": "192.0.2.1", "MAX_VOLUME": "0", "VOLUME_STEP": "0", "ADMIN_PIN": "1"},
        {"SONOS_IP": "192.0.2.1", "ADMIN_PIN": "change-me"},
    ]
    problem_codes = {p.code for env in environments for p in load_settings(env).problems}
    assert problem_codes == {
        "sonos_not_configured",
        "sonos_ip_invalid",
        "max_volume_invalid",
        "volume_step_invalid",
        "admin_pin_missing",
        "admin_pin_too_short",
        "admin_pin_placeholder",
    }
    for code in problem_codes:
        assert f"error.{code}" in CATALOGUES[DEFAULT_LANG]


# -- completeness: every code and key used anywhere has a text ------------------------

import re  # noqa: E402
from pathlib import Path  # noqa: E402

PACKAGE = Path(__file__).resolve().parent.parent / "muckebox"
CATALOGUE = CATALOGUES[DEFAULT_LANG]


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def test_every_error_class_has_a_text():
    from muckebox.covers import CoverError
    from muckebox.library import LibraryError
    from muckebox.sonos.errors import SonosError

    codes = {CoverError.code}
    for base in (SonosError, LibraryError):
        codes |= {base.code} | {sub.code for sub in _subclasses(base)}
    missing = sorted(code for code in codes if f"error.{code}" not in CATALOGUE)
    assert not missing


def test_every_literal_error_code_in_the_code_has_a_text():
    pattern = re.compile(r'(?:ApiError\(\d+, |Unavailable\()"([a-z_]+)"')
    codes = {
        code
        for path in PACKAGE.rglob("*.py")
        for code in pattern.findall(path.read_text(encoding="utf-8"))
    }
    assert "busy" in codes and "config_error" in codes  # the scan works
    missing = sorted(code for code in codes if f"error.{code}" not in CATALOGUE)
    assert not missing


def test_every_key_used_by_pages_and_scripts_exists():
    keys = set()
    for path in (PACKAGE / "templates").glob("*.html"):
        keys |= set(re.findall(r'data-i18n(?:-label|-placeholder)?="([^"]+)"', path.read_text()))
    for path in (PACKAGE / "static" / "js").glob("*.js"):
        keys |= set(re.findall(r'\bt\("([a-z_]+\.[a-z_]+)"', path.read_text()))
        keys |= set(re.findall(r'"((?:kids|admin|status)\.[a-z_]+)"', path.read_text()))
    assert len(keys) > 20  # the scan works
    missing = sorted(key for key in keys if key not in CATALOGUE)
    assert not missing


def test_every_unplayable_reason_and_warning_has_a_text():
    from muckebox.sonos.fake import demo_favorites

    reasons = {"no_resource", "tv_input", "broken_metadata"}
    reasons |= {f.reason for f in demo_favorites() if f.reason}
    warnings = {"cover_missing", "title_missing", "sharelink_experimental"}
    missing = [f"reason.{r}" for r in reasons if f"reason.{r}" not in CATALOGUE]
    missing += [f"warning.{w}" for w in warnings if f"warning.{w}" not in CATALOGUE]
    assert not missing
