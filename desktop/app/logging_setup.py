"""Lightweight app file-logging (TODO #10 — Diagnostics 'Logs' tab).

The PySide client had no file logging; this adds a rotating handler writing to
``logs/app.log`` so the Diagnostics window shows REAL events and problems are
recoverable after the fact. Initialised once from ``build_app``.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import paths

_INITED = False


def log_path() -> str:
    return str(paths.LOGS_DIR / "app.log")


def init_logging(level: int = logging.INFO) -> str:
    """Attach a rotating file handler to the root logger (idempotent)."""
    global _INITED
    paths.ensure_runtime_dirs()
    path = log_path()
    if _INITED:
        return path
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
    _INITED = True
    logging.getLogger("app").info("logging initialised -> %s", path)
    return path
