# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Thread-safe cache of everything the kids view shows."""

from __future__ import annotations

import copy
import threading
from typing import Any


class StateCache:
    def __init__(self, **initial: Any) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(initial)
        self._rev = 0

    def update(self, **sections: Any) -> None:
        with self._lock:
            changed = False
            for key, value in sections.items():
                if self._data.get(key) != value:
                    self._data[key] = copy.deepcopy(value)
                    changed = True
            if changed:
                self._rev += 1

    def get(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key))

    def snapshot(self) -> tuple[dict[str, Any], int]:
        with self._lock:
            return copy.deepcopy(self._data), self._rev
