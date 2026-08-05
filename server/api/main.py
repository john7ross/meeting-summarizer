#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI Server для Meeting Summarizer
Серверный режим работы приложения
"""
import os
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy import select
from fastapi import (
    FastAPI, HTTPException, Depends, UploadFile, File, Query, WebSocket,
    WebSocketDisconnect, WebSocketException, status,
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Добавляем путь к backend
backend_path = Path(__file__).parent.parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from ..auth.auth_handler import get_current_user, create_access_token, decode_access_token
from ..database.db import get_db, init_db, AsyncSessionLocal
from ..database.models import User, Meeting
from .routes import admin, auth, meetings, settings, queue, engines, rag, templates
from .websocket import manager

# Определяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'


def _app_version() -> str:
    """Load the public version from the repository manifest."""
    try:
        with (backend_path.parent / "package.json").open("r", encoding="utf-8") as fh:
            value = json.load(fh).get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, ValueError):
        pass
    return "0.0.0"


APP_VERSION = _app_version()


async def reconcile_orphaned_jobs() -> int:
    """Return meetings stranded by a previous process to a state the user can act on.

    Restored to the furthest point whose artefacts actually survived:
      * a transcript on disk -> ``completed`` is wrong (no summary), so mark it
        ``failed`` with a reason; Regenerate can then re-run summary+analysis.
      * no transcript -> back to ``uploaded``, so Process can simply start again.
    """
    restored = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Meeting).where(
            Meeting.status.in_(("processing", "queued"))))).scalars().all()
        for m in rows:
            has_transcript = bool(m.transcript_path and Path(m.transcript_path).exists())
            if has_transcript:
                m.status = "failed"
                m.error_message = ("Interrupted: the server stopped while this "
                                   "meeting was being processed. The transcript "
                                   "was kept - use Regenerate to finish it.")
            else:
                m.status = "uploaded"
                m.error_message = None
                m.progress = 0
            m.stage = None
            m.eta_seconds = None
            restored += 1
        if restored:
            await db.commit()
    return restored


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle (replaces the deprecated @app.on_event hooks)."""
    from ..processing.queue import processing_queue
    print("=" * 60)
    print("Meeting Summarizer Server")
    print("=" * 60)
    print(f"Server Mode: {SERVER_MODE}")
    print(f"Backend Path: {backend_path}")

    await init_db()
    print("Database initialized")

    # Nothing survives the process, so any row still marked queued/processing is
    # a job whose worker died with the previous instance. Left alone it spins in
    # the cabinet forever: the queue no longer owns it, so Cancel answers
    # "neither queued nor processing", and a meeting orphaned during
    # TRANSCRIPTION has no transcript either, so Regenerate refuses too - the
    # user is left with no way out at all. Reconcile on boot instead.
    orphaned = await reconcile_orphaned_jobs()
    if orphaned:
        print(f"Recovered {orphaned} interrupted meeting(s) left by a previous run")

    # Installation-wide settings the administrator chose. Without this the worker
    # count reverted to auto-detection on every restart, so load management set by
    # an admin silently undid itself.
    from ..database.db import AsyncSessionLocal
    from .routes.admin import apply_server_settings, load_server_settings
    async with AsyncSessionLocal() as db:
        server_settings = await load_server_settings(db)
    apply_server_settings(server_settings)
    if server_settings.get("parallelWorkers"):
        print(f"Server settings applied: workers={processing_queue.max_workers} "
              f"(admin-configured)")

    for name in ("uploads", "transcripts"):
        d = Path(name)
        d.mkdir(exist_ok=True)
        print(f"{name.capitalize()} directory: {d.absolute()}")

    await processing_queue.start()
    status = processing_queue.get_status()
    origin = ("admin-configured" if server_settings.get("parallelWorkers")
              else "auto-detected for this hardware")
    print(f"Processing queue started: {status['max_workers']} workers ({origin})")
    print("=" * 60)

    yield

    await processing_queue.stop()
    print("Processing queue stopped")


app = FastAPI(
    title="Meeting Summarizer API",
    description="API для обработки видео встреч и генерации саммари",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# CORS для веб-клиента
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Подключаем роуты
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(meetings.router, prefix="/api/meetings", tags=["Meetings"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(queue.router, prefix="/api/queue", tags=["Queue"])
app.include_router(engines.router, prefix="/api/engines", tags=["Engines"])
app.include_router(admin.router, prefix="/api/admin", tags=["Administration"])
app.include_router(rag.router, prefix="/api/rag", tags=["RAG & Search"])
app.include_router(templates.router, prefix="/api/templates", tags=["Templates"])

# Статические файлы (Web UI)
web_static_path = Path(__file__).parent.parent / "web"
if web_static_path.exists():
    app.mount("/static", StaticFiles(directory=str(web_static_path)), name="static")


@app.middleware("http")
async def _revalidate_static(request, call_next):
    """Force browsers to revalidate static assets via ETag instead of serving a
    heuristically-"fresh" stale copy. StaticFiles sends ETag/Last-Modified but no
    Cache-Control, so after a UI update the browser can keep old JS/CSS until a
    hard refresh. ``no-cache`` = cache but always revalidate (fast 304 when
    unchanged, fresh 200 when the file changed)."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/dashboard.html"):
        response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/")
async def root():
    """Главная страница - отдаем Web UI"""
    web_index = Path(__file__).parent.parent / "web" / "index.html"
    if web_index.exists():
        return FileResponse(web_index)
    return {"message": "Meeting Summarizer API", "version": APP_VERSION, "mode": "server" if SERVER_MODE else "desktop"}


@app.get("/dashboard.html")
async def dashboard_page():
    """Кабинет пользователя (auth.js редиректит сюда после входа)."""
    web_dashboard = Path(__file__).parent.parent / "web" / "dashboard.html"
    if web_dashboard.exists():
        return FileResponse(web_dashboard)
    return JSONResponse(status_code=404, content={"detail": "dashboard not found"})

@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Health check. Served under both paths: every other endpoint lives under
    ``/api``, so probes and reverse proxies reasonably look for ``/api/health``
    and used to get a 404 from a server that was in fact perfectly healthy."""
    return {"status": "healthy", "version": APP_VERSION,
            "mode": "server" if SERVER_MODE else "desktop"}

@app.get("/api/info")
async def api_info():
    """Информация о API"""
    return {
        "name": "Meeting Summarizer API",
        "version": APP_VERSION,
        "mode": "server" if SERVER_MODE else "desktop",
        "endpoints": {
            "auth": "/api/auth",
            "meetings": "/api/meetings",
            "settings": "/api/settings",
            "websocket": "/ws/{meeting_id}"
        }
    }


@app.websocket("/ws/{meeting_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    meeting_id: int,
    token: str | None = Query(default=None),
):
    """WebSocket endpoint для real-time обновлений обработки"""
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
    except (HTTPException, TypeError, ValueError):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Invalid credentials")

    # A valid user may subscribe only to their own meeting.  Do this before
    # accept()/manager.connect(), otherwise an unauthorised socket can receive
    # progress broadcasts during the authentication gap.
    async with AsyncSessionLocal() as db:
        owned = (await db.execute(select(Meeting.id).where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id))).scalar_one_or_none()
    if owned is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Meeting not found")

    await manager.connect(websocket, meeting_id)

    try:
        # Отправляем начальное сообщение
        await websocket.send_json({
            "type": "connected",
            "meeting_id": meeting_id,
            "message": "Connected to meeting updates"
        })

        # Держим соединение открытым
        while True:
            # Ждем сообщений от клиента (ping/pong)
            data = await websocket.receive_text()

            # Можно обрабатывать команды от клиента если нужно
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket, meeting_id)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket, meeting_id)

if __name__ == "__main__":
    # Запуск сервера
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=not SERVER_MODE,  # В продакшене отключаем reload
        log_level="info"
    )
