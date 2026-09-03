"""Installation-wide administration: settings that apply to EVERY user, and the
shared on-disk resources (ASR models, engine packages).

Why these are not per-user settings: the worker count is load management for the
whole machine, and models/engines are files on disk that every account then uses.
A regular user must not be able to raise the concurrency for everybody or start a
multi-gigabyte download, and an administrator's choice has to survive a restart -
the worker count used to live only in the queue object's memory and reverted to the
auto-detected value on every boot.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.auth_handler import get_current_admin_user, get_current_user
from ...database.db import get_db
from ...database.models import ServerSettings, User
from ...processing.queue import processing_queue
from ...runtime import backend_python

router = APIRouter()

# Transcription engines run as subprocesses under the runtime that owns them,
# never in this process - the server venv is deliberately torch-free (see
# processing/worker.py). Probing or installing with ``sys.executable`` therefore
# answers about the wrong interpreter: every engine reported "not installed"
# while transcription worked fine, and Install would have pulled torch/CUDA into
# the venv that is kept torch-free on purpose, without fixing anything.
_REPO = Path(__file__).resolve().parents[3]
_PY = backend_python()
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

# 0 = decide from the machine (the queue's own auto-detection).
SERVER_DEFAULTS: Dict[str, Any] = {"parallelWorkers": 0}

MAX_WORKERS = 4


class ServerSettingsUpdate(BaseModel):
    """Partial update. A field missing HERE would be silently discarded."""
    parallelWorkers: Optional[int] = None


async def _row(db: AsyncSession) -> Optional[ServerSettings]:
    return (await db.execute(select(ServerSettings).where(
        ServerSettings.id == 1))).scalar_one_or_none()


async def load_server_settings(db: AsyncSession) -> Dict[str, Any]:
    """Stored installation settings merged over the defaults."""
    merged = dict(SERVER_DEFAULTS)
    row = await _row(db)
    if row and row.settings_json:
        try:
            merged.update(json.loads(row.settings_json))
        except (ValueError, TypeError):
            pass
    return merged


def apply_server_settings(settings: Dict[str, Any]) -> None:
    """Push the installation settings into the running services."""
    workers = int(settings.get("parallelWorkers", 0) or 0)
    if workers:
        processing_queue.set_max_workers(max(1, min(MAX_WORKERS, workers)))


@router.get("/settings")
async def get_server_settings(current_user: User = Depends(get_current_admin_user),
                             db: AsyncSession = Depends(get_db)):
    settings = await load_server_settings(db)
    settings["effectiveWorkers"] = processing_queue.max_workers
    return {"settings": settings}


@router.put("/settings")
async def update_server_settings(patch: ServerSettingsUpdate,
                                 current_user: User = Depends(get_current_admin_user),
                                 db: AsyncSession = Depends(get_db)):
    """Change installation-wide settings. Applies immediately AND persists."""
    incoming = patch.model_dump(exclude_unset=True, exclude_none=True)
    if "parallelWorkers" in incoming:
        value = int(incoming["parallelWorkers"])
        if value < 0 or value > MAX_WORKERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"parallelWorkers must be 0 (auto) to {MAX_WORKERS}")
        incoming["parallelWorkers"] = value
    current = await load_server_settings(db)
    current.update(incoming)
    stored = {k: v for k, v in current.items() if k in SERVER_DEFAULTS}
    row = await _row(db)
    if row:
        row.settings_json = json.dumps(stored, ensure_ascii=False)
        row.updated_by = current_user.id
    else:
        db.add(ServerSettings(id=1, settings_json=json.dumps(stored, ensure_ascii=False),
                              updated_by=current_user.id))
    await db.commit()
    apply_server_settings(stored)
    stored["effectiveWorkers"] = processing_queue.max_workers
    return {"settings": stored}


# ── shared resources: engines and their Python packages ─────────────────────
# Mirrors desktop/packaging/installer.py: the registry itself carries no
# dependency metadata, so the mapping lives in one place per front-end. Kept in
# sync deliberately - a self-test asserts the two agree.
ENGINE_PACKAGES: Dict[str, list] = {
    "whisper": ["openai-whisper"],
    "faster-whisper": ["faster-whisper"],
    "whisperx": ["whisperx", "faster-whisper", "ctranslate2"],
    "vosk": ["vosk"],
    "whisper-cpp": ["pywhispercpp"],
    "sherpa": ["sherpa-onnx", "onnxruntime"],
    "funasr": ["sherpa-onnx", "onnxruntime"],
}


PACKAGE_MODULE = {"openai-whisper": "whisper", "faster-whisper": "faster_whisper",
                  "sherpa-onnx": "sherpa_onnx", "onnxruntime": "onnxruntime",
                  "pywhispercpp": "pywhispercpp", "ctranslate2": "ctranslate2",
                  "whisperx": "whisperx", "vosk": "vosk"}


async def _modules_present(modules: list) -> Dict[str, bool]:
    """Which of these modules import in the EMBEDDED runtime (one subprocess)."""
    probe = ("import importlib.util,json,sys;"
             "print(json.dumps({m: importlib.util.find_spec(m) is not None "
             "for m in json.loads(sys.argv[1])}))")
    try:
        proc = await asyncio.create_subprocess_exec(
            str(_PY), "-c", probe, json.dumps(modules),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_ENV)
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"Backend interpreter not runnable at {_PY}: {exc}")
    out, _err = await proc.communicate()
    if proc.returncode != 0:
        # Report honestly rather than pretending everything is missing.
        raise HTTPException(status_code=500,
                            detail="Could not probe the embedded runtime at "
                                   f"{_PY}: {_err.decode(errors='replace')[-200:]}")
    return json.loads(out.decode(errors="replace") or "{}")


@router.get("/engines/packages")
async def engine_packages(current_user: User = Depends(get_current_user)):
    """Which Python packages each engine needs, and whether they import.

    Asked of the embedded runtime, because that is where the engines actually run.
    """
    modules = sorted({PACKAGE_MODULE.get(p, p)
                      for packages in ENGINE_PACKAGES.values() for p in packages})
    present = await _modules_present(modules)
    out = []
    for engine, packages in ENGINE_PACKAGES.items():
        missing = [p for p in packages
                   if not present.get(PACKAGE_MODULE.get(p, p), False)]
        out.append({"engine": engine, "packages": packages,
                    "installed": not missing, "missing": missing})
    return {"engines": out}


@router.post("/engines/{engine}/install", status_code=status.HTTP_202_ACCEPTED)
async def install_engine(engine: str,
                         current_user: User = Depends(get_current_admin_user)):
    """Install an engine's Python packages into the EMBEDDED runtime.

    Admin only, and deliberately so: this changes the installation for every user.
    It must target ``backend/python`` - the interpreter the transcription
    subprocesses use - not this process, whose venv stays torch-free.
    """
    packages = ENGINE_PACKAGES.get(engine)
    if not packages:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Unknown engine: {engine}")
    from .engines import start_background_job
    return await start_background_job(
        key=f"install:{engine}",
        argv=[str(_PY), "-m", "pip", "install", "--upgrade", *packages],
        label=f"Installing {engine}: {', '.join(packages)}")
