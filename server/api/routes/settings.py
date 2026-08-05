#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Settings routes — structured per-user settings.

Stored as a JSON blob (``UserSettings.settings_json``, the same shape the worker
reads), but the API is TYPED: GET returns the defaults merged with the user's saved
values; PUT accepts a partial ``SettingsData`` and merges it in. Unknown keys the
client sends are preserved too, so future desktop keys keep working.
"""
import os
import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ...database.db import get_db
from ...database.models import User, UserSettings
from ...auth.auth_handler import get_current_user
from ..schemas import SettingsUpdate, SettingsResponse

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'
if not SERVER_MODE:
    raise RuntimeError("settings routes should not be imported in desktop mode")

# Reuse the desktop's Qt-free template library for the full default prompt.
import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from desktop.app.backend import templates as _tpl   # noqa: E402
from backend.rag_catalogs import CatalogConfigError, validate_shared_key  # noqa: E402

router = APIRouter()

# Defaults shown to a user who has saved nothing yet (mirror the desktop config).
DEFAULT_SETTINGS = {
    "transcriptionEngine": "faster-whisper", "whisperModel": "medium",
    "transcriptionLanguage": "ru", "outputLanguage": "auto",
    "whisperDevice": "auto", "transcriptionHint": "",
    "diarizationBackend": "sherpa", "hfToken": "",
    "aiProvider": "local", "aiModel": "", "apiKey": "", "localEndpoint": "",
    # Local agent CLI (provider="agent"). The cabinet offered nine providers to the
    # desktop's ten: "agent" was missing here, and without these two keys the
    # backend had no command to run even if it had been selectable.
    "agentCommand": "", "agentCwd": "",
    "analysisSource": "transcript",
    "aiTimeout": 0, "disableReasoning": False, "aiRetries": 3, "aiRetryDelay": 60,
    "chunkChars": 0, "chunkingEnabled": False, "gpuHandoff": False, "llamaPort": 8080,
    "youtubeCookiesBrowser": "auto",
    "googleSheetsIntegration": False, "googleSheetsUrl": "", "googleSheetsToken": "",
    # Contextual memory groups meetings by project. Without a default project id
    # the cabinet could only tag a meeting AFTER upload - by which time processing
    # had already run - so switching the feature on changed nothing at all.
    "useContextualMemory": False, "projectId": "",
    # Whether the prompt gets its speaker-aware variant when diarisation is on.
    # The desktop has had this since the beginning; the cabinet had no such switch.
    "useSpeakerPrompt": True,
    # Obsidian export. The vault is a path ON THE SERVER, so this suits a machine
    # the user also works on (or a synced/mounted vault); the notes are written by
    # the same Qt-free module the desktop uses, so the two produce identical files.
    "obsidianIntegration": False, "obsidianVaultPath": "",
    "createPeopleNotes": False, "createTopicNotes": False,
    "createDataviewQueries": False, "updateMeetingIndex": False,
    "enableMarkdownExport": False,
    "ragCatalogMode": "isolated", "ragSharedCatalogKey": "",
    # Which embedder the knowledge base uses. Absent here, the cabinet silently
    # took whatever the RAG layer defaulted to, with no way to match the desktop.
    "ragEmbeddingBackend": "sentence-transformers",
    "ragEmbeddingModel": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "prompt": _tpl.default_prompt("ru"),
    "extractActionItems": True, "analyzeSentiment": True,
    "categorizeAutomatically": True, "generateFollowupQuestions": True,
    "generateFormalProtocol": True, "advancedSettings": {},
}


def _parse(row) -> dict:
    stored = {}
    if row and row.settings_json:
        try:
            stored = json.loads(row.settings_json)
        except (ValueError, TypeError):
            stored = {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


async def _get_row(db, user_id):
    return (await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()


@router.get("/", response_model=SettingsResponse)
async def get_settings(current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Typed settings: defaults merged with the user's saved values."""
    row = await _get_row(db, current_user.id)
    return SettingsResponse(user_id=current_user.id, settings=_parse(row),
                            updated_at=getattr(row, "updated_at", None))


@router.put("/", response_model=SettingsResponse)
async def update_settings(patch: SettingsUpdate,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Merge a partial settings update into the user's saved settings."""
    row = await _get_row(db, current_user.id)
    current = {}
    if row and row.settings_json:
        try:
            current = json.loads(row.settings_json)
        except (ValueError, TypeError):
            current = {}
    # Only apply the fields the client actually sent.
    incoming = patch.model_dump(exclude_unset=True, exclude_none=True)
    prospective = dict(DEFAULT_SETTINGS)
    prospective.update(current)
    prospective.update(incoming)
    if prospective.get("ragCatalogMode") == "shared":
        try:
            validate_shared_key(prospective.get("ragSharedCatalogKey", ""))
        except CatalogConfigError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=str(exc))
    current.update(incoming)
    blob = json.dumps(current, ensure_ascii=False)
    if row:
        row.settings_json = blob
    else:
        row = UserSettings(user_id=current_user.id, settings_json=blob)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return SettingsResponse(user_id=current_user.id, settings=_parse(row),
                            updated_at=getattr(row, "updated_at", None))


@router.delete("/", response_model=SettingsResponse)
async def reset_settings(current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Reset to defaults."""
    row = await _get_row(db, current_user.id)
    if row:
        row.settings_json = "{}"
        await db.commit()
        await db.refresh(row)
    return SettingsResponse(user_id=current_user.id, settings=dict(DEFAULT_SETTINGS),
                            updated_at=getattr(row, "updated_at", None))
