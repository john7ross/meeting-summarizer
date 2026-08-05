#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG (semantic knowledge base) + plain-text transcript search.

Semantic RAG reuses the verified ``backend/rag.py`` (chromadb) run in the embedded
runtime as a subprocess. The default is a per-user ``rag_data/u<id>`` catalog.
An account can instead opt into a capability-keyed catalog shared with desktop.
Embeddings use the user's own settings (their local model's /v1/embeddings or a
cloud key). Plain-text search reuses the desktop Qt-free ``textsearch`` in-process.
"""
import os
import json
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'
if not SERVER_MODE:
    raise RuntimeError("rag routes should not be imported in desktop mode")

from ...database.db import get_db
from ...database.models import User, Meeting, UserSettings
from ...auth.auth_handler import get_current_user
from backend.rag_catalogs import CatalogConfigError, server_catalog_dir
from ...runtime import backend_python

router = APIRouter()

_REPO = Path(__file__).resolve().parents[3]
_PY = backend_python()
_RAG = _REPO / "backend" / "rag.py"


def _settings_dict(settings_json: str) -> dict:
    try:
        data = json.loads(settings_json or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _rag_dir(user_id: int, settings_json: str = "{}") -> str:
    try:
        return str(server_catalog_dir(
            user_id, _settings_dict(settings_json), create=True))
    except CatalogConfigError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _settings_json(db, user_id: int) -> str:
    row = (await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    return row.settings_json if (row and row.settings_json) else "{}"


async def _run_rag(user_id: int, settings_json: str, *args) -> dict:
    cmd = [str(_PY), str(_RAG), *args,
           "--rag-dir", _rag_dir(user_id, settings_json), "--settings", settings_json]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"Backend interpreter not runnable at {_PY}: {exc}")
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(status_code=500,
                            detail=(err.decode(errors="replace")[-400:] or "RAG command failed"))
    try:
        return json.loads(out.decode(errors="replace"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="RAG returned unparseable output")


async def _owned_meeting(db, meeting_id, user_id):
    m = (await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == user_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return m


@router.post("/meetings/{meeting_id}", status_code=status.HTTP_201_CREATED)
async def rag_add(meeting_id: int, current_user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    """Index a meeting (summary + transcript) into the user's knowledge base."""
    m = await _owned_meeting(db, meeting_id, current_user.id)
    if not m.summary_path and not m.transcript_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Nothing to index (no summary or transcript)")
    args = ["add", "--doc-id", str(meeting_id), "--project", m.project or "",
            "--title", m.original_filename or "",
            "--date", (m.processed_at.isoformat() if m.processed_at else "")]
    if m.summary_path:
        args += ["--summary-file", m.summary_path]
    if m.transcript_path:
        args += ["--transcript-file", m.transcript_path]
    return await _run_rag(current_user.id, await _settings_json(db, current_user.id), *args)


@router.get("/search")
async def rag_search(q: str = Query(..., min_length=1), project: str = Query(""),
                     top_k: int = Query(5, ge=1, le=50),
                     current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Semantic search over the user's knowledge base."""
    return await _run_rag(current_user.id, await _settings_json(db, current_user.id),
                          "search", "--query", q, "--project", project, "--top-k", str(top_k))


@router.get("/library")
async def rag_library(project: str = Query(""),
                      current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """List indexed documents in the user's knowledge base."""
    return await _run_rag(current_user.id, await _settings_json(db, current_user.id),
                          "list", "--project", project)


@router.get("/stats")
async def rag_stats(current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    return await _run_rag(current_user.id, await _settings_json(db, current_user.id), "stats")


@router.delete("/meetings/{meeting_id}")
async def rag_delete(meeting_id: int, current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    await _owned_meeting(db, meeting_id, current_user.id)   # authorize
    return await _run_rag(current_user.id, await _settings_json(db, current_user.id),
                          "delete", "--doc-id", str(meeting_id))


@router.get("/textsearch")
async def text_search(q: str = Query(..., min_length=1), regex: bool = Query(False),
                      case: bool = Query(False), speaker: str = Query(""),
                      current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Plain-text/regex search across the user's transcripts (no embeddings)."""
    import sys as _sys
    if str(_REPO) not in _sys.path:
        _sys.path.insert(0, str(_REPO))
    from desktop.app.backend import textsearch
    meetings = (await db.execute(select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.transcript_path.isnot(None)))).scalars().all()
    results = []
    try:
        for m in meetings:
            if not m.transcript_path or not Path(m.transcript_path).exists():
                continue
            text = Path(m.transcript_path).read_text(encoding="utf-8", errors="replace")
            hits = textsearch.search_in_text(text, q, use_regex=regex,
                                             case_sensitive=case, speaker_filter=speaker)
            if hits:
                results.append({"meeting_id": m.id, "filename": m.original_filename,
                                "hit_count": len(hits), "hits": hits})
    except Exception as e:  # invalid regex etc.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"query": q, "meeting_count": len(results), "results": results}
