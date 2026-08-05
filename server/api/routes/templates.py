#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt templates — the built-in library (read-only) + a user's saved templates.

The built-in library (12 meeting types × RU/EN, each with a speaker-aware variant)
is reused verbatim from the desktop's Qt-free ``templates`` module. User templates
are per-user rows in the DB (multi-user), with edit/rename/delete.
"""
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'
if not SERVER_MODE:
    raise RuntimeError("templates routes should not be imported in desktop mode")

from ...database.db import get_db
from ...database.models import User, UserTemplate
from ...auth.auth_handler import get_current_user
from ..schemas import TemplateCreate, TemplateResponse

router = APIRouter()

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from desktop.app.backend import templates as _tpl   # noqa: E402


@router.get("/")
async def list_templates(lang: str = Query("ru"), speaker: bool = Query(False),
                         current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Built-in library (speaker-aware if requested) + the user's saved templates."""
    builtin = _tpl.builtin_templates(lang if lang in ("ru", "en") else "ru", speaker)
    rows = (await db.execute(select(UserTemplate).where(
        UserTemplate.user_id == current_user.id).order_by(UserTemplate.name))).scalars().all()
    user = [{"id": r.id, "name": r.name, "prompt": r.prompt, "builtin": False} for r in rows]
    return {"builtin": builtin, "user": user}


@router.post("/", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(data: TemplateCreate,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Save a new user template (replaces an existing one with the same name)."""
    name = data.name.strip()
    existing = (await db.execute(select(UserTemplate).where(
        UserTemplate.user_id == current_user.id, UserTemplate.name == name))).scalar_one_or_none()
    if existing:
        existing.prompt = data.prompt or ""
        row = existing
    else:
        row = UserTemplate(user_id=current_user.id, name=name, prompt=data.prompt or "")
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(template_id: int, data: TemplateCreate,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Edit / rename a user template."""
    row = (await db.execute(select(UserTemplate).where(
        UserTemplate.id == template_id,
        UserTemplate.user_id == current_user.id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    row.name = data.name.strip()
    row.prompt = data.prompt or ""
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(UserTemplate).where(
        UserTemplate.id == template_id,
        UserTemplate.user_id == current_user.id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await db.delete(row)
    await db.commit()
    return None
