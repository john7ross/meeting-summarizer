"""Application version loaded from the repository's package manifest."""
from __future__ import annotations

import json
from pathlib import Path


def _read_version() -> str:
    manifest = Path(__file__).resolve().parents[2] / "package.json"
    try:
        with manifest.open("r", encoding="utf-8") as fh:
            value = json.load(fh).get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, ValueError):
        pass
    return "0.0.0"


APP_VERSION = _read_version()
