# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Errors raised by the Sonos backend.

Every error carries a stable ``code`` that the API passes to clients (and
that the i18n catalogue translates as ``error.<code>``).
"""

from __future__ import annotations


class SonosError(Exception):
    """Base class for all errors from the Sonos backend."""

    code = "sonos_error"
    #: True if the speaker could not be reached at all. Only these errors
    #: open the circuit breaker; a slow or rejected command does not.
    connection_problem = False

    def __init__(self, message: str = "", *, upnp_code: int | None = None) -> None:
        super().__init__(message or self.code)
        self.upnp_code = upnp_code


class SonosUnreachable(SonosError):
    code = "sonos_unreachable"
    connection_problem = True


class UpnpDisabled(SonosError):
    """The speaker rejects UPnP control (Sonos app: UPnP switched off)."""

    code = "upnp_disabled"
    connection_problem = True


class RoomNotFound(SonosError):
    code = "room_not_found"
    connection_problem = True


class SonosTimeout(SonosError):
    """The speaker was reachable but did not answer in time."""

    code = "sonos_timeout"


class GroupProblem(SonosError):
    code = "group_problem"


class ServiceUnavailable(SonosError):
    """Sonos could not load the content from the music service."""

    code = "service_unavailable"


class NotPlayable(SonosError):
    code = "not_playable"


class PlaybackFailed(SonosError):
    code = "playback_failed"


class CommandRejected(SonosError):
    """The speaker rejected a command with a UPnP error."""

    code = "command_rejected"


class ActionNotAvailable(SonosError):
    """The command is not available right now (e.g. "next" on radio)."""

    code = "action_not_available"


# UPnP error codes that the AVTransport service uses.
UPNP_TRANSITION_NOT_AVAILABLE = 701
UPNP_NO_CONTENTS = 702
UPNP_ILLEGAL_SEEK_TARGET = 711
UPNP_ILLEGAL_MIME_TYPE = 714
UPNP_INVALID_ARGS = 402
UPNP_SERVICE_ERROR = 800
UPNP_SERVICE_ERROR_2 = 804

#: Errors that mean "this content cannot be played this way": worth trying
#: the other playback route once.
ROUTE_ERRORS = frozenset({UPNP_INVALID_ARGS, UPNP_ILLEGAL_MIME_TYPE})
