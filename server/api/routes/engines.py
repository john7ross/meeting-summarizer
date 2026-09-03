#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Engines & models — catalog + downloads for the personal cabinet.

Reuses the SAME catalog the desktop UI consumes: ``backend/models_cli.py`` run in
the embedded runtime. Read-only listing is available to any authenticated user;
triggering a model download (a shared on-disk resource) is admin-only. Downloads
run as background tasks and stream JSON-lines progress into an in-memory tracker
polled via ``GET /api/engines/downloads``.
"""
import os
import json
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'
if not SERVER_MODE:
    raise RuntimeError("engines routes should not be imported in desktop mode")

from ...auth.auth_handler import get_current_user, get_current_admin_user
from ...database.models import User
from ...runtime import backend_python

router = APIRouter()

_REPO = Path(__file__).resolve().parents[3]
_PY = backend_python()
_CLI = _REPO / "backend" / "models_cli.py"

# Force the embedded runtime to emit UTF-8 on stdout; otherwise on Windows it
# uses the console codepage (cp1251) and Cyrillic catalog labels arrive mojibake.
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

# In-memory download tracker: "engine/model" -> {engine, model, status, percent, detail}
_downloads: dict = {}


async def _run_cli(*args) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            str(_PY), str(_CLI), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_ENV)
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"Backend interpreter not runnable at {_PY}: {exc}")
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(status_code=500,
                            detail=(err.decode(errors="replace")[-300:] or "models_cli failed"))
    return out.decode(errors="replace")


@router.get("/")
async def list_engines(current_user: User = Depends(get_current_user)):
    """Catalog: engines + per-language models with on-disk availability."""
    return json.loads(await _run_cli("catalog"))


async def _download_task(engine: str, model: str):
    key = f"{engine}/{model}"
    _downloads[key] = {"engine": engine, "model": model,
                       "status": "downloading", "percent": 0, "detail": ""}
    try:
        proc = await asyncio.create_subprocess_exec(
            str(_PY), str(_CLI), "download", "--engine", engine, "--model", model,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_ENV)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                data = json.loads(line.decode(errors="replace").strip())
            except json.JSONDecodeError:
                continue
            if data.get("event") == "progress":
                _downloads[key]["percent"] = data.get("percent", 0)
                _downloads[key]["detail"] = data.get("detail", "")
            elif data.get("event") == "done":
                _downloads[key]["status"] = "completed" if data.get("ok") else "failed"
        await proc.wait()
        if proc.returncode != 0 and _downloads[key]["status"] == "downloading":
            _downloads[key]["status"] = "failed"
        elif _downloads[key]["status"] == "downloading":
            _downloads[key]["status"] = "completed"
            _downloads[key]["percent"] = 100
    except Exception as e:  # noqa: BLE001
        _downloads[key]["status"] = "failed"
        _downloads[key]["detail"] = str(e)


async def _job_task(key: str, argv: list, label: str):
    """Run any admin job (pip install, …) into the SAME tracker as downloads, so
    one progress feed covers every shared-resource operation."""
    _downloads[key] = {"engine": key.split(":")[0], "model": label,
                       "status": "downloading", "percent": 0, "detail": label}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=_ENV)
        tail = ""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            tail = line.decode(errors="replace").strip()[:200]
            _downloads[key]["detail"] = tail
        await proc.wait()
        ok = proc.returncode == 0
        _downloads[key]["status"] = "completed" if ok else "failed"
        _downloads[key]["percent"] = 100 if ok else _downloads[key]["percent"]
        if not ok:
            _downloads[key]["detail"] = tail or f"exit {proc.returncode}"
    except Exception as e:  # noqa: BLE001
        _downloads[key]["status"] = "failed"
        _downloads[key]["detail"] = str(e)


async def start_background_job(key: str, argv: list, label: str) -> dict:
    if _downloads.get(key, {}).get("status") == "downloading":
        return {"status": "already_running", "key": key}
    asyncio.create_task(_job_task(key, argv, label))
    return {"status": "started", "key": key}


@router.get("/{engine}/models/{model}/update-check")
async def check_model_update(engine: str, model: str,
                             current_user: User = Depends(get_current_user)):
    """Is a newer build of this model available? ``models_cli check-update`` has
    always been able to answer this; nothing in the cabinet ever asked."""
    return json.loads(await _run_cli("check-update", "--engine", engine,
                                     "--model", model))


@router.post("/{engine}/models/{model}/download", status_code=status.HTTP_202_ACCEPTED)
async def download_model(engine: str, model: str,
                         current_user: User = Depends(get_current_admin_user)):
    """Start a background model download (admin only — shared on-disk resource)."""
    key = f"{engine}/{model}"
    if _downloads.get(key, {}).get("status") == "downloading":
        return {"status": "already_downloading", "key": key}
    asyncio.create_task(_download_task(engine, model))
    return {"status": "started", "key": key}


@router.get("/downloads")
async def download_status(current_user: User = Depends(get_current_user)):
    """Current/last model-download jobs (poll for progress)."""
    return {"downloads": list(_downloads.values())}
