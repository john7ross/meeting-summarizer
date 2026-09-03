#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket manager for real-time processing updates
"""
import os
import json
from typing import Dict, Set
from fastapi import WebSocket

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("websocket should not be imported in desktop mode")


class ConnectionManager:
    """Управление WebSocket соединениями"""

    def __init__(self):
        # meeting_id -> set of websockets
        self.active_connections: Dict[int, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, meeting_id: int):
        """Подключение клиента к обновлениям встречи"""
        await websocket.accept()

        if meeting_id not in self.active_connections:
            self.active_connections[meeting_id] = set()

        self.active_connections[meeting_id].add(websocket)

    def disconnect(self, websocket: WebSocket, meeting_id: int):
        """Отключение клиента"""
        if meeting_id in self.active_connections:
            self.active_connections[meeting_id].discard(websocket)

            # Удаляем пустые множества
            if not self.active_connections[meeting_id]:
                del self.active_connections[meeting_id]

    async def send_update(self, meeting_id: int, message: dict):
        """Отправка обновления всем подключенным клиентам"""
        if meeting_id in self.active_connections:
            # Копируем множество чтобы избежать проблем при отключении во время отправки
            connections = self.active_connections[meeting_id].copy()

            for websocket in connections:
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    print(f"Error sending to websocket: {e}")
                    self.disconnect(websocket, meeting_id)

    async def broadcast_status(self, meeting_id: int, status: str, details: str = ""):
        """Отправка обновления статуса"""
        await self.send_update(meeting_id, {
            "type": "status",
            "meeting_id": meeting_id,
            "status": status,
            "details": details
        })

    async def broadcast_progress(self, meeting_id: int, stage: str, progress: int, details: str = ""):
        """Отправка прогресса обработки"""
        await self.send_update(meeting_id, {
            "type": "progress",
            "meeting_id": meeting_id,
            "stage": stage,
            "progress": progress,
            "details": details
        })

    async def broadcast_error(self, meeting_id: int, error: str):
        """Отправка ошибки"""
        await self.send_update(meeting_id, {
            "type": "error",
            "meeting_id": meeting_id,
            "error": error
        })

    async def broadcast_completed(self, meeting_id: int):
        """Отправка уведомления о завершении"""
        await self.send_update(meeting_id, {
            "type": "completed",
            "meeting_id": meeting_id
        })


# Глобальный экземпляр
manager = ConnectionManager()
