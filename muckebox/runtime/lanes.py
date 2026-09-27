# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Worker lanes: one thread each, running jobs strictly one after another."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

log = logging.getLogger(__name__)

_STOP = object()


class Lane:
    """A worker thread with a job queue and a periodic idle task.

    Jobs submitted with :meth:`submit` run in order on the lane's thread.
    Whenever no job arrives for ``interval`` seconds, ``idle_task`` runs
    (used for polling the speaker). Exceptions never kill the thread.
    """

    def __init__(self, name: str, idle_task: Callable[[], None] | None, interval: float) -> None:
        self.name = name
        self._idle_task = idle_task
        self._interval = interval
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running_job = False

    @property
    def busy(self) -> bool:
        return self._running_job or not self._queue.empty()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"lane-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout)

    def submit(self, job: Callable[[], Any]) -> Future:
        future: Future = Future()
        self._queue.put((job, future))
        return future

    def _run(self) -> None:
        next_idle = time.monotonic()
        while True:
            try:
                item = self._queue.get(timeout=max(0.0, next_idle - time.monotonic()))
            except queue.Empty:
                self._safe(self._idle_task)
                next_idle = time.monotonic() + self._interval
                continue
            if item is _STOP:
                return
            job, future = item
            if not future.set_running_or_notify_cancel():
                continue
            self._running_job = True
            try:
                future.set_result(job())
            except BaseException as exc:  # reported through the future
                future.set_exception(exc)
            finally:
                self._running_job = False

    def _safe(self, task: Callable[[], None] | None) -> None:
        if task is None:
            return
        try:
            task()
        except Exception:
            log.exception("Idle task of lane %s failed", self.name)


class InlineLane:
    """Runs jobs immediately in the caller's thread (for tests)."""

    def __init__(self, name: str = "inline") -> None:
        self.name = name
        self.busy = False

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass

    def submit(self, job: Callable[[], Any]) -> Future:
        future: Future = Future()
        try:
            future.set_result(job())
        except BaseException as exc:  # reported through the future
            future.set_exception(exc)
        return future
