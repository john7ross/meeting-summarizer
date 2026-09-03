"""Subprocess argv with per-process environment values for sensitive inputs."""
from __future__ import annotations

import os


class SecureCommand(list):
    """A normal argv list plus environment overrides not exposed in argv."""

    def __init__(self, parts=(), *, environment=None):
        super().__init__(parts)
        self.environment = {
            str(key): str(value)
            for key, value in (environment or {}).items()
            if value is not None and str(value) != ""
        }

    def process_environment(self) -> dict:
        env = dict(os.environ)
        env.update(self.environment)
        return env
