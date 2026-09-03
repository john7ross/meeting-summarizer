"""Portable path resolution for the native desktop client.

Every path is derived from the project root so the app stays fully portable:
no installation and no hard-coded user paths. When frozen with PyInstaller,
the executable's own directory is used as the root instead.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def project_root() -> Path:
    """Return the meeting-summarizer project root directory."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-dir build: resources sit next to the executable.
        return Path(sys.executable).resolve().parent
    # Dev layout: this file is <root>/desktop/app/paths.py
    return Path(__file__).resolve().parents[2]


ROOT = project_root()

BACKEND_DIR = ROOT / "backend"
CONFIG_DIR = ROOT / "config"
TRANSCRIPTS_DIR = ROOT / "transcripts"
LOGS_DIR = ROOT / "logs"
RAG_DIR = ROOT / "rag_knowledge_base"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
# The journal of processing RUNS, separate from the meeting archive above: the
# queue is rebuilt from history.json, so deleting a row there must not delete the
# record of the file having been processed.
PROCESSING_HISTORY_FILE = CONFIG_DIR / "processing_history.json"

PROCESSOR_SCRIPT = BACKEND_DIR / "processor.py"
AI_CLIENT_SCRIPT = BACKEND_DIR / "ai_client.py"
RAG_SCRIPT = BACKEND_DIR / "rag.py"
MODELS_CLI_SCRIPT = BACKEND_DIR / "models_cli.py"
URL_DOWNLOAD_SCRIPT = BACKEND_DIR / "url_download.py"
LOCAL_AI_SCRIPT = BACKEND_DIR / "local_ai.py"

FFMPEG_DIR = BACKEND_DIR / "FFmpeg"


def python_executable() -> Path:
    """Path to the bundled portable Python that runs the backend subprocesses."""
    exe = BACKEND_DIR / "python" / "python.exe"
    if exe.exists():
        return exe
    # Dev fallback: reuse the interpreter currently running.
    return Path(sys.executable)


def _ffmpeg_tool(name: str) -> str:
    """Path to a bundled FFmpeg tool ('ffmpeg'/'ffprobe'), falling back to PATH."""
    exe = FFMPEG_DIR / f"{name}.exe"
    if exe.exists():
        return str(exe)
    found = shutil.which(name)
    return found or name


def ffmpeg_executable() -> str:
    return _ffmpeg_tool("ffmpeg")


def ffprobe_executable() -> str:
    return _ffmpeg_tool("ffprobe")


def ensure_runtime_dirs() -> None:
    """Create the writable runtime directories if they do not yet exist."""
    for directory in (CONFIG_DIR, TRANSCRIPTS_DIR, LOGS_DIR, RAG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def rag_dir(settings: dict | None = None) -> Path:
    """Resolve the configured isolated/shared RAG catalog safely."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from rag_catalogs import desktop_catalog_dir
    return desktop_catalog_dir(settings or {}, create=True)


def generate_rag_shared_key() -> str:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from rag_catalogs import generate_shared_key
    return generate_shared_key()


def validate_rag_shared_key(value: str) -> str:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from rag_catalogs import validate_shared_key
    return validate_shared_key(value)
