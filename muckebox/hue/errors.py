# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Errors from the Hue bridge; ``code`` is translated as ``error.<code>``."""

from __future__ import annotations


class HueError(Exception):
    code = "hue_error"
    #: True if the bridge could not be reached at all (opens the breaker).
    connection_problem = False

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class HueUnreachable(HueError):
    code = "hue_unreachable"
    connection_problem = True


class HueLinkButton(HueError):
    """Pairing: the button on the bridge has not been pressed (yet)."""

    code = "hue_link_button"


class HueCertificateChanged(HueError):
    """The bridge presents another certificate than at pairing."""

    code = "hue_certificate_changed"
    connection_problem = True


class HueUnauthorized(HueError):
    """The bridge no longer knows Muckebox's key (deleted in the Hue app)."""

    code = "hue_unauthorized"


class HueNotFound(HueError):
    """A room or scene that is not there (any more)."""

    code = "hue_not_found"
