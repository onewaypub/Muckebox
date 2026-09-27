# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP layer: Flask app factory, API endpoints and pages."""

from .app import create_app

__all__ = ["create_app"]
