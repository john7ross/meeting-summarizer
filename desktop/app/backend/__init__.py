"""Adapters that drive the existing Python backend via its CLI subprocesses.

Nothing here re-implements transcription or AI logic; these modules only build
the correct command lines and parse the backend's stdout, keeping the verified
backend untouched and giving the UI clean, Qt-free primitives to call.
"""
