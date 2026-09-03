#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Which interpreter owns the engines (torch, whisper, RAG).

The full build ships an embedded runtime at ``backend/python``; the min build
installs the engines into a Python the user already had, and ``INSTALL.bat``
records which one in ``config/interpreter.txt``. Hardcoding the embedded path
made the engines, RAG and admin-package routes answer a bare 500 on every min
installation - the path simply does not exist there, so spawning it raised
FileNotFoundError before any handler could report anything useful.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def backend_python() -> Path:
    """Path to the interpreter the backend subprocesses must run under."""
    embedded = _REPO / "backend" / "python" / "python.exe"
    if embedded.exists():
        return embedded
    recorded = _REPO / "config" / "interpreter.txt"
    if recorded.exists():
        candidate = Path(recorded.read_text(encoding="utf-8").strip())
        if candidate.exists():
            return candidate
    # Dev checkout. A deployment with neither of the above keeps the old
    # behaviour: it answers about this process, which for the torch-free server
    # venv means an honest "engine not installed" rather than a crash.
    return Path(sys.executable)
