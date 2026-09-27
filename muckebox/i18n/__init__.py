# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""User interface texts.

Every text shown to users lives in a message catalogue, one module per
language, keyed by stable English keys such as ``error.sonos_unreachable``.
The server sends keys (for example API error codes), and clients translate
them with the same catalogue. Placeholders use ``{name}`` syntax.

To add a language, copy ``de.py``, translate the values and register the
module in ``CATALOGUES``.
"""

from __future__ import annotations

import re

from . import de

DEFAULT_LANG = "de"

CATALOGUES: dict[str, dict[str, str]] = {"de": de.MESSAGES}

_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def translate(key: str, lang: str = DEFAULT_LANG, **params: object) -> str:
    """Return the text for ``key`` with ``{name}`` placeholders filled in.

    Unknown keys return the key itself, so a missing translation is visible
    but never breaks the UI. Unknown placeholders are left untouched.
    """
    text = CATALOGUES.get(lang, CATALOGUES[DEFAULT_LANG]).get(key, key)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(params[name]) if name in params else match.group(0)

    return _PLACEHOLDER_RE.sub(fill, text)


def placeholders(text: str) -> set[str]:
    """Return the placeholder names used in ``text``."""
    return set(_PLACEHOLDER_RE.findall(text))
