"""Native PySide desktop client for meeting-summarizer.

This package re-uses the existing, verified Python backend (transcription
engines, AI client, exporters, RAG) by driving its command-line subprocesses,
and rebuilds only the UI layer natively in PySide6. Built incrementally,
component by component; no feature from the Electron app is dropped.
"""

__all__ = ["paths", "config"]
