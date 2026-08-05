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
from .logging_setup import init_logging
from .ui.main_window import MainWindow


def build_app(argv: Optional[list] = None):
    """Construct the QApplication, settings, store, queue and window."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)

    init_logging()
    settings = config.load_settings()
    paths.ensure_runtime_dirs()
    store = HistoryStore()

    workers = resolve_workers(settings.get("parallelWorkers", "auto"))

    def runner_factory(entry_id, video_path):
        return JobRunner(entry_id, video_path, settings, store)

    queue = PipelineQueue(workers, runner_factory)

    # Real per-job log lines for the Diagnostics 'Logs' tab (non-invasive: via the
    # queue's existing signals, so pipeline internals are untouched).
    _log = logging.getLogger("app.pipeline")
    queue.status_changed.connect(
        lambda jid, st: _log.info("job %s -> %s", jid, getattr(st, "value", st)))
    queue.job_finished.connect(
        lambda jid, ok, err: _log.info("job %s finished ok=%s", jid, ok)
        if ok else _log.error("job %s failed: %s", jid, err))

    window = MainWindow(
        settings, store, queue,
        language=settings.get("language", "ru"),
        theme=settings.get("theme", "dark"),
        persist_ui_preferences=True,
    )
    return app, window


def main() -> int:
    app, window = build_app()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
