"""Launcher for the native desktop client.

    backend\\python\\python.exe desktop\\run.py

Adds this folder to sys.path so the ``app`` package imports cleanly in both the
dev tree and a frozen build.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
