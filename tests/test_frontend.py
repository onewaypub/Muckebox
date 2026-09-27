# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static frontend files are served."""

import pytest


@pytest.mark.parametrize(
    "path",
    ["/static/js/kids.js", "/static/js/admin.js", "/static/css/kids.css", "/static/css/admin.css"],
)
def test_static_files(client, path):
    with client.get(path) as response:
        assert response.status_code == 200
