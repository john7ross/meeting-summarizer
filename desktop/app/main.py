"""Application bootstrap: wire settings, history, the pipeline queue and the
main window together. ``build_app`` is kept separate from ``main`` so it can be
constructed headlessly in tests without entering the event loop.
"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtWidgets import QApplication

import logging

from . import config, paths
from .core.history import HistoryStore
from .core.pipeline import JobRunner, PipelineQueue
from .core.queue_manager import resolve_workers
from .core.run_history import RunHistoryStore
from .logging_setup import init_logging
from .ui.main_window import MainWindow


def log_job_finished(log, job_id, ok: bool, error: str) -> None:
    """One log line per finished job, at the level the outcome deserves.

    A user pressing Cancel is not a fault: it used to be written as
    ``ERROR job … failed: __cancelled__``, so the Diagnostics log made a deliberate
    action look like a defect (and every log scan counted it as one).
    """
    if ok:
        log.info("job %s finished ok=True", job_id)
    elif error == "__cancelled__":
        log.info("job %s cancelled by the user", job_id)
    else:
        log.error("job %s failed: %s", job_id, error)


def build_app(argv: Optional[list] = None):
    """Construct the QApplication, settings, store, queue and window."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)

    init_logging()
    settings = config.load_settings()
    paths.ensure_runtime_dirs()
    store = HistoryStore()
    # The processing journal is separate from the meeting archive: removing a
    # meeting from the queue must not erase the record of it being processed.
    # Runs left open by a kill/crash belong to a previous process — close them
    # before this one starts writing.
    run_store = RunHistoryStore()
    run_store.mark_interrupted()

    workers = resolve_workers(settings.get("parallelWorkers", "auto"))

    def runner_factory(entry_id, video_path):
        return JobRunner(entry_id, video_path, settings, store, run_store=run_store)

    queue = PipelineQueue(workers, runner_factory)

    # Real per-job log lines for the Diagnostics 'Logs' tab (non-invasive: via the
    # queue's existing signals, so pipeline internals are untouched).
    _log = logging.getLogger("app.pipeline")
    queue.status_changed.connect(
        lambda jid, st: _log.info("job %s -> %s", jid, getattr(st, "value", st)))
    queue.job_finished.connect(
        lambda jid, ok, err: log_job_finished(_log, jid, ok, err))

    window = MainWindow(
        settings, store, queue,
        language=settings.get("language", "ru"),
        theme=settings.get("theme", "dark"),
        persist_ui_preferences=True,
        run_store=run_store,
    )
    return app, window


def main() -> int:
    app, window = build_app()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
