"""Scheduler: runs up to N jobs concurrently, routing every event by id.

The manager keeps at most ``max_concurrency`` active workers (from the
``parallelWorkers`` setting: explicit 2/4/6/8, or a value resolved from the
machine for ``auto``). When one finishes it pulls the next queued job. Because
each job has its own worker keyed by id, parallel jobs never cross-contaminate
each other's status.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .worker import TranscriptionWorker


def resolve_workers(parallel_workers, *, cuda: bool = False) -> int:
    """Translate the ``parallelWorkers`` setting into a concrete count."""
    if isinstance(parallel_workers, int):
        return max(1, parallel_workers)
    text = str(parallel_workers).strip().lower()
    if text.isdigit():
        return max(1, int(text))
    # "auto": be conservative on a single GPU (VRAM-bound), use cores on CPU.
    import os
    cores = os.cpu_count() or 4
    if cuda:
        return 1
    return max(1, min(4, cores // 2))


class QueueManager(QObject):
    job_progress = Signal(object, object)   # job_id, ProgressEvent
    job_done = Signal(object, object)        # job_id, ResultEvent
    active_changed = Signal(int)         # number of active workers
    all_done = Signal()

    def __init__(self, max_concurrency: int = 1, cwd=None, parent=None):
        super().__init__(parent)
        self.max_concurrency = max(1, int(max_concurrency))
        self._cwd = cwd
        self._pending: deque = deque()
        self._active: dict[int, TranscriptionWorker] = {}

    def enqueue(self, job_id: int, command) -> None:
        self._pending.append((int(job_id), list(command)))
        self._pump()

    def active_count(self) -> int:
        return len(self._active)

    def pending_count(self) -> int:
        return len(self._pending)

    def cancel(self, job_id: int) -> None:
        worker = self._active.get(job_id)
        if worker:
            worker.stop()

    # -- internal ------------------------------------------------------
    def _pump(self) -> None:
        changed = False
        while len(self._active) < self.max_concurrency and self._pending:
            job_id, command = self._pending.popleft()
            worker = TranscriptionWorker(job_id, command, cwd=self._cwd, parent=self)
            worker.progress.connect(self.job_progress)
            worker.done.connect(self._on_worker_done)
            self._active[job_id] = worker
            changed = True
            worker.start()
        if changed:
            self.active_changed.emit(len(self._active))

    def _on_worker_done(self, job_id: int, result) -> None:
        self.job_done.emit(job_id, result)
        self._active.pop(job_id, None)
        self.active_changed.emit(len(self._active))
        self._pump()
        if not self._active and not self._pending:
            self.all_done.emit()
