#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Queue status routes
"""
import os
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.db import get_db
from ...database.models import User
from ...auth.auth_handler import get_current_user, get_current_admin_user
from ...processing.queue import processing_queue

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("queue routes should not be imported in desktop mode")

router = APIRouter()


@router.get("/status")
async def get_queue_status(current_user: User = Depends(get_current_user)):
    """Получение статуса очереди обработки"""
    result = processing_queue.get_status()
    if current_user.role != "admin":
        # Internal meeting ids belong to users and must not be disclosed as a
        # global cross-tenant list.  The dashboard uses only aggregate counts.
        result.pop("processing_meetings", None)
    return result


@router.post("/workers/{count}")
async def set_workers_count(
    count: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Изменение количества параллельных воркеров (только для админов).

    PERSISTED as an installation-wide setting: it used to live only in the queue
    object, so an administrator's load-management decision was undone by the next
    restart, which silently reverted to hardware auto-detection.
    """
    if count < 1 or count > 4:
        return {"error": "Workers count must be between 1 and 4"}

    from .admin import ServerSettingsUpdate, update_server_settings
    await update_server_settings(ServerSettingsUpdate(parallelWorkers=count),
                                 current_user=current_user, db=db)
    return {
        "message": f"Workers count set to {count}",
        "status": processing_queue.get_status()
    }
