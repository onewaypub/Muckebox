# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Creating tiles from share links (completed together with link parsing)."""

from __future__ import annotations

from .errors import ApiError


def create_tile(services, url: str):
    raise ApiError(422, "sharelink_unsupported")
