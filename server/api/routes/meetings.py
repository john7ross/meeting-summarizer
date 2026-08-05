#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meetings routes
"""
import os
import sys
import asyncio
import ipaddress
import json
import shutil
import socket
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from fastapi import (APIRouter, Depends, HTTPException, status, UploadFile, File,
                     Form, Query)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, delete as sql_delete

from ...database.db import get_db
from ...database.models import User, Meeting, Artifact, ProcessingLog
from ...auth.auth_handler import get_current_user
from ..schemas import (MeetingCreate, MeetingUpdate, MeetingResponse,
                       MeetingListResponse, MeetingStatusResponse, SpeakerRename,
                       MeetingFromUrl, SegmentCut, TranscriptUpdate, ObsidianExport)

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("meetings routes should not be imported in desktop mode")

router = APIRouter()

# Директории для хранения файлов
UPLOAD_DIR = Path("uploads")
TRANSCRIPTS_DIR = Path("transcripts")
UPLOAD_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 ** 3)))
ALLOW_PRIVATE_URLS = os.getenv("ALLOW_PRIVATE_URLS", "false").lower() == "true"


def _safe_upload_name(filename: str | None) -> str:
    """Return a basename safe on Windows and POSIX, never a client path."""
    raw = (filename or "upload").replace("\\", "/")
    name = Path(raw).name.strip().strip(".")
    return name or "upload"


async def _user_settings(db: AsyncSession, user_id: int) -> dict:
    """The user's saved settings merged over the API defaults."""
    from ...database.models import UserSettings
    from .settings import DEFAULT_SETTINGS
    merged = dict(DEFAULT_SETTINGS)
    row = (await db.execute(select(UserSettings).where(
        UserSettings.user_id == user_id))).scalar_one_or_none()
    if row and row.settings_json:
        try:
            merged.update(json.loads(row.settings_json))
        except (ValueError, TypeError):
            pass
    return merged


async def _default_project(db: AsyncSession, user_id: int) -> str:
    """The user's configured default project id, or "" when unset."""
    return str((await _user_settings(db, user_id)).get("projectId", "") or "").strip()


def _remove_meeting_directory(meeting_id: int) -> bool:
    """Remove only this meeting's server-owned artifact directory.

    Individual artifact paths do not cover trace files and generated exports,
    so deleting only the paths stored on ``Meeting`` leaks data over time.
    The resolved-parent check keeps this recursive removal confined to
    ``TRANSCRIPTS_DIR/<numeric meeting id>``.
    """
    root = TRANSCRIPTS_DIR.resolve()
    target = (root / str(int(meeting_id))).resolve()
    if target.parent != root or target.name != str(int(meeting_id)):
        raise ValueError(f"Unsafe meeting directory: {target}")
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def _host_is_public(hostname: str) -> bool:
    """Resolve every address and reject local/private/special destinations."""
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
    return True


async def _validate_source_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="URL credentials are not allowed")
    if not ALLOW_PRIVATE_URLS and not await asyncio.to_thread(
            _host_is_public, parsed.hostname):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Private, local, or unresolvable URL destinations are not allowed")


@router.post("/upload", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def upload_meeting(
    file: UploadFile = File(...),
    process: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload a recording.

    ``process=false`` stores the file WITHOUT queueing it, which is what the
    cabinet does when the user wants to cut one long recording into separate
    meetings first (the desktop client shows its Trim dialog at the same point).
    """

    # Проверяем расширение файла
    allowed_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.mp3', '.wav', '.m4a'}
    original_name = _safe_upload_name(file.filename)
    file_ext = Path(original_name).suffix.lower()

    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(allowed_extensions)}"
        )

    # Генерируем уникальное имя файла
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{current_user.id}_{timestamp}_{uuid.uuid4().hex[:12]}_{original_name}"
    file_path = UPLOAD_DIR / filename

    # Save in bounded streaming chunks. The limit is checked while reading, so a
    # forged or missing Content-Length cannot bypass it.
    try:
        file_size = 0
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload exceeds MAX_UPLOAD_BYTES ({MAX_UPLOAD_BYTES})")
                f.write(chunk)
    except HTTPException:
        if file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        # Удаляем частично загруженный файл
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {str(e)}"
        )

    # Создаем запись в БД
    new_meeting = Meeting(
        user_id=current_user.id,
        filename=filename,
        original_filename=original_name,
        status="uploaded",
        video_path=str(file_path),
        file_size=file_size,
        # Stamp the configured default project so contextual memory has something to
        # group by. Tagging was only possible AFTER upload, i.e. after processing had
        # already started, which left the feature permanently inert.
        project=await _default_project(db, current_user.id),
        uploaded_at=datetime.utcnow()
    )

    db.add(new_meeting)
    await db.commit()
    await db.refresh(new_meeting)

    if process:
        from ...processing.queue import processing_queue
        await processing_queue.add_meeting(new_meeting.id)

    return new_meeting


@router.post("/from-url", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting_from_url(
    data: MeetingFromUrl,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a meeting from a video URL (YouTube / file server / …).

    No file is uploaded now: the media is downloaded during processing (worker),
    then it goes through the exact same pipeline as an uploaded video."""
    url = data.url.strip()
    await _validate_source_url(url)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # A human-ish placeholder name; the worker replaces it with the real title.
    display = Path(url.split("?")[0]).name or "video"
    new_meeting = Meeting(
        user_id=current_user.id,
        filename=f"{current_user.id}_{timestamp}_url",
        original_filename=display,
        status="uploaded",
        source_url=url,
        project=data.project or await _default_project(db, current_user.id),
        uploaded_at=datetime.utcnow(),
    )
    db.add(new_meeting)
    await db.commit()
    await db.refresh(new_meeting)

    from ...processing.queue import processing_queue
    await processing_queue.add_meeting(new_meeting.id)
    return new_meeting


@router.get("/", response_model=MeetingListResponse)
async def list_meetings(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    status_filter: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Список встреч пользователя"""

    # Базовый запрос
    query = select(Meeting).where(Meeting.user_id == current_user.id)

    # Фильтр по статусу
    if status_filter:
        query = query.where(Meeting.status == status_filter)

    # Сортировка по дате создания (новые первые)
    query = query.order_by(desc(Meeting.created_at))

    # Подсчет общего количества
    count_query = select(Meeting).where(Meeting.user_id == current_user.id)
    if status_filter:
        count_query = count_query.where(Meeting.status == status_filter)

    total_result = await db.execute(count_query)
    total = len(total_result.scalars().all())

    # Пагинация
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    meetings = result.scalars().all()

    return {"total": total, "meetings": meetings}


# Declared BEFORE "/{meeting_id}": FastAPI matches in order, and a literal path
# placed after the parameterised one would be swallowed as an id and 422.
@router.get("/stats")
async def meeting_stats(current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Archive statistics for the signed-in user.

    Mirrors the desktop Statistics dialog (``stats_dialog.aggregate``) one metric
    for one metric, so both front-ends report the same numbers: totals, how many
    meetings have each artifact, the transcript word count, and the breakdown by
    status and by project.
    """
    rows = (await db.execute(select(Meeting).where(
        Meeting.user_id == current_user.id))).scalars().all()
    artifacts = (await db.execute(select(Artifact.meeting_id, Artifact.kind).join(
        Meeting, Meeting.id == Artifact.meeting_id).where(
        Meeting.user_id == current_user.id))).all()
    with_summary = {mid for mid, kind in artifacts if kind == "summary"}
    with_analysis = {mid for mid, kind in artifacts if kind == "analysis"}

    words = 0
    with_tx = 0
    by_status: dict = {}
    by_project: dict = {}
    for m in rows:
        if m.transcript_path:
            with_tx += 1
            try:
                path = Path(m.transcript_path)
                if path.exists():
                    words += len(path.read_text(encoding="utf-8",
                                                errors="replace").split())
            except OSError:
                pass
        st = m.status or "-"
        by_status[st] = by_status.get(st, 0) + 1
        proj = getattr(m, "project", "") or ""
        by_project[proj] = by_project.get(proj, 0) + 1

    return {"total": len(rows), "with_tx": with_tx,
            "with_sum": len(with_summary), "with_an": len(with_analysis),
            "words": words, "by_status": by_status, "by_project": by_project}


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Получение информации о встрече"""

    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
    )
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    return meeting


@router.get("/{meeting_id}/status", response_model=MeetingStatusResponse)
async def get_meeting_status(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Лёгкий статус для поллинга кабинета: статус, прогресс %, стадия, ETA."""
    meeting = (await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id, Meeting.user_id == current_user.id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


@router.patch("/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
    meeting_id: int,
    meeting_data: MeetingUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Обновление информации о встрече"""

    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
    )
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    # Обновляем поля
    update_data = meeting_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(meeting, field, value)

    await db.commit()
    await db.refresh(meeting)

    return meeting


async def _purge_meeting(db: AsyncSession, meeting: Meeting) -> None:
    """Delete one meeting's files, directory, artifacts and logs.

    Shared by the single delete and the bulk clear so the two can never drift -
    a second, hand-copied cleanup is how artifacts and logs got orphaned before.
    """
    for file_path in (meeting.video_path, meeting.transcript_path,
                      meeting.summary_path, meeting.analysis_path):
        if file_path and Path(file_path).exists():
            try:
                Path(file_path).unlink()
            except Exception as e:  # noqa: BLE001
                print(f"Failed to delete file {file_path}: {e}")
    try:
        _remove_meeting_directory(meeting.id)
    except Exception as e:  # noqa: BLE001
        print(f"Failed to delete meeting directory {meeting.id}: {e}")
    await db.execute(sql_delete(Artifact).where(Artifact.meeting_id == meeting.id))
    await db.execute(sql_delete(ProcessingLog).where(
        ProcessingLog.meeting_id == meeting.id))
    await db.delete(meeting)


@router.delete("/finished")
async def clear_finished_meetings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove every meeting of this user that is not currently processing.

    Declared BEFORE ``/{meeting_id}`` or the path is parsed as an id. Deleting
    recordings one at a time was the only option in the cabinet, while the
    desktop has had a "clear the queue" action; a run in progress is kept and
    reported back rather than silently killed.
    """
    from ...processing.queue import processing_queue
    rows = (await db.execute(select(Meeting).where(
        Meeting.user_id == current_user.id))).scalars().all()
    deleted, skipped = 0, 0
    for meeting in rows:
        if processing_queue.is_processing(meeting.id):
            skipped += 1
            continue
        await _purge_meeting(db, meeting)
        deleted += 1
    await db.commit()
    return {"deleted": deleted, "skipped": skipped}


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meeting(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Удаление встречи"""

    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
    )
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    # A meeting that is still being processed must be stopped BEFORE its files
    # go away, or the worker keeps transcribing into a deleted directory and
    # then fails writing to a row that no longer exists. The desktop refuses the
    # removal outright; here the delete implies the cancel and waits for it.
    from ...processing.queue import processing_queue
    if await processing_queue.cancel_meeting(meeting_id) != "idle":
        for _ in range(40):                      # bounded: 10s at 0.25s
            if not processing_queue.is_processing(meeting_id):
                break
            await asyncio.sleep(0.25)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The meeting is still stopping; try again in a moment")

    # Files, the per-meeting directory (trace files and on-demand exports live
    # there and are not Meeting columns), and the artifact/log rows, which are
    # NOT ORM-cascaded: deleting only the meeting left them behind, SQLite reused
    # the freed id, and the next meeting inherited another user's version history.
    await _purge_meeting(db, meeting)
    await db.commit()

    return None


@router.get("/{meeting_id}/download/{file_type}")
async def download_file(
    meeting_id: int,
    file_type: str,
    version: int = Query(0, description="0 = latest; else a specific summary/analysis version"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Скачивание файла встречи (video, transcript, summary, analysis)"""

    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
    )
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    # Определяем путь к файлу
    file_path_map = {
        "video": meeting.video_path,
        "transcript": meeting.transcript_path,
        "summary": meeting.summary_path,
        "analysis": meeting.analysis_path
    }

    if file_type not in file_path_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(file_path_map.keys())}"
        )

    file_path = file_path_map[file_type]
    if file_type in ("summary", "analysis") and version > 0:
        artifact = (await db.execute(select(Artifact).where(
            Artifact.meeting_id == meeting_id,
            Artifact.kind == file_type,
            Artifact.version == version,
        ))).scalar_one_or_none()
        if not artifact:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{file_type} version {version} not found",
            )
        file_path = artifact.path

    if not file_path or not Path(file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{file_type.capitalize()} file not found"
        )
    if Path(file_path).stat().st_size == 0:
        # A run that recognised no speech leaves an empty transcript behind; handing
        # the user a zero-byte file is worse than saying there is nothing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"The {file_type} file is empty - there is nothing to download")

    # Offer the name the user recognises. The stored name carries an internal
    # prefix ("5_20260727_003647_c9ee8152c661_Запись….webm") that meant nothing to
    # them; artifacts get the recording's stem plus what they are.
    stored = Path(file_path)
    if file_type == "video":
        nice = _safe_upload_name(meeting.original_filename) or stored.name
    else:
        stem = Path(meeting.original_filename or stored.stem).stem
        suffix = "" if version <= 0 else f"_v{version}"
        nice = f"{stem}_{file_type}{suffix}{stored.suffix}"
    return FileResponse(
        path=file_path,
        filename=nice,
        media_type="application/octet-stream"
    )


# Reuse the desktop's Qt-free exporter (raw/summary/analysis -> txt/md/json/html/pdf/docx).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPORT_KINDS = ("raw", "summary", "analysis")
_EXPORT_FORMATS = ("txt", "md", "json", "html", "pdf", "docx")


@router.get("/{meeting_id}/export/{kind}/{fmt}")
async def export_meeting(
    meeting_id: int,
    kind: str,
    fmt: str,
    lang: str = Query("ru"),
    version: int = Query(0, description="0 = latest; else a specific summary/analysis version"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Export an artifact (raw/summary/analysis) as txt/md/json/html/pdf/docx."""
    import json
    import sys as _sys
    if kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid kind. Allowed: {', '.join(_EXPORT_KINDS)}")
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid format. Allowed: {', '.join(_EXPORT_FORMATS)}")

    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id,
                              Meeting.user_id == current_user.id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    selected_version = 1
    if kind in ("summary", "analysis"):
        artifact_query = select(Artifact).where(
            Artifact.meeting_id == meeting_id,
            Artifact.kind == kind,
        )
        if version > 0:
            artifact_query = artifact_query.where(Artifact.version == version)
        else:
            artifact_query = artifact_query.order_by(desc(Artifact.version))
        art = (await db.execute(artifact_query)).scalars().first()
        if version > 0 and not art:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"{kind} version {version} not found")
        if art:
            src = art.path
            selected_version = art.version
        else:
            # Compatibility with meetings created before artifact version rows
            # existed: their current summary/analysis is implicitly v1.
            src = {"summary": meeting.summary_path,
                   "analysis": meeting.analysis_path}.get(kind)
    else:
        src = meeting.transcript_path
    if not src or not Path(src).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No {kind} to export yet")

    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))
    from desktop.app.backend import exporter
    from desktop.app.backend import media as exporter_media

    if kind == "analysis":
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    else:
        data = Path(src).read_text(encoding="utf-8", errors="replace")

    stem = Path(meeting.original_filename).stem
    # An export must not depend on which front-end produced it: the desktop fills
    # the same characteristics block, deriving length and word count from the
    # transcript when the row has none. Without this the web cabinet's files show
    # an empty duration for meetings whose length the app displays.
    transcript_text = ""
    if meeting.transcript_path and Path(meeting.transcript_path).exists():
        transcript_text = Path(meeting.transcript_path).read_text(
            encoding="utf-8", errors="replace")
    meta = {"video_name": meeting.original_filename,
            "language": lang if lang in ("ru", "en") else "ru",
            "duration": (meeting.duration or ""
                         or exporter_media.duration_from_transcript(transcript_text)),
            "version": selected_version}
    if transcript_text:
        meta["wordCount"] = len(transcript_text.split())
    out_dir = Path(src).parent / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = exporter.default_export_path(out_dir, stem, kind, selected_version, fmt)
    try:
        written = exporter.export(kind, data, fmt, out_path, meta)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")
    return FileResponse(path=written, filename=Path(written).name,
                        media_type="application/octet-stream")


@router.post("/{meeting_id}/obsidian")
async def export_to_obsidian(meeting_id: int, data: ObsidianExport,
                             current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Write the selected notes into the user's Obsidian vault.

    The vault is a directory ON THE SERVER (a machine the user also works on, or a
    synced/mounted vault). Notes are produced by the same Qt-free module the desktop
    uses, so both front-ends write identical files, and the note follows the version
    the user picked - not merely the newest one.
    """
    import sys as _sys
    meeting = (await db.execute(select(Meeting).where(
        Meeting.id == meeting_id,
        Meeting.user_id == current_user.id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting not found")
    settings_data = await _user_settings(db, current_user.id)
    vault = str(settings_data.get("obsidianVaultPath") or "").strip()
    if not vault:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Obsidian vault is configured. Set the vault path in Settings.")
    if not Path(vault).is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The configured Obsidian vault does not exist on the server: {vault}")

    kinds = tuple(k for k in (data.kinds or ["summary", "analysis"])
                  if k in ("summary", "analysis", "raw"))
    if not kinds:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Nothing selected to export")

    async def _artifact(kind: str, version: int):
        """(text, version) for a kind at the requested version (0 = newest)."""
        query = select(Artifact).where(Artifact.meeting_id == meeting_id,
                                       Artifact.kind == kind)
        query = (query.where(Artifact.version == version) if version > 0
                 else query.order_by(desc(Artifact.version)))
        art = (await db.execute(query)).scalars().first()
        path = art.path if art else {"summary": meeting.summary_path,
                                     "analysis": meeting.analysis_path}.get(kind)
        if not path or not Path(path).exists():
            return "", 1
        return Path(path).read_text(encoding="utf-8", errors="replace"), (
            art.version if art else 1)

    summary_text, summary_version = await _artifact("summary", data.summary_version)
    analysis_raw, analysis_version = await _artifact("analysis", data.analysis_version)
    transcript_text = ""
    if meeting.transcript_path and Path(meeting.transcript_path).exists():
        transcript_text = Path(meeting.transcript_path).read_text(
            encoding="utf-8", errors="replace")
    try:
        analysis = json.loads(analysis_raw) if analysis_raw else {}
    except ValueError:
        analysis = {}
    if not isinstance(analysis, dict):
        analysis = {}

    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))
    from desktop.app.backend import obsidian as _obsidian

    stem = Path(meeting.video_path or meeting.original_filename or "meeting").stem
    try:
        written = await asyncio.to_thread(
            _obsidian.export_to_obsidian, vault,
            stem=stem, video_name=meeting.original_filename or stem,
            summary_text=summary_text, analysis=analysis, settings=settings_data,
            duration=meeting.duration or "", summary_version=summary_version,
            analysis_version=analysis_version, transcript_text=transcript_text,
            language=settings_data.get("outputLanguage") if
            settings_data.get("outputLanguage") in ("ru", "en") else "ru",
            kinds=kinds)
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    # Report whichever note was actually written: reporting only the summary made
    # the other kinds look like dead buttons.
    return {"vault": written.get("vault"),
            "written": {k: written.get(k) for k in ("summary", "analysis", "transcript")
                        if written.get(k)}}


@router.get("/{meeting_id}/trace")
async def meeting_trace(meeting_id: int,
                        current_user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """Per-stage timings of a finished run, for the status timeline.

    The backend already writes ``<name>_trace.json`` beside the artifacts, but
    nothing served it, so the cabinet could only ever show a status badge while
    the desktop showed every stage and how long it took.
    """
    result = await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == current_user.id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting not found")
    folder = TRANSCRIPTS_DIR / str(int(meeting_id))
    traces = sorted(folder.glob("*_trace.json")) if folder.is_dir() else []
    if not traces:
        return {"spans": [], "duration": 0}
    try:
        data = json.loads(traces[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"spans": [], "duration": 0}
    spans = [{"name": s.get("name", ""), "duration": s.get("duration")}
             for s in (data.get("spans") or []) if s.get("name")]
    return {"spans": spans, "duration": data.get("duration") or 0}


@router.get("/{meeting_id}/versions")
async def list_versions(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List summary/analysis versions (from Regenerate) for a meeting."""
    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id,
                              Meeting.user_id == current_user.id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    rows = (await db.execute(
        select(Artifact).where(Artifact.meeting_id == meeting_id)
        .order_by(Artifact.kind, Artifact.version))).scalars().all()
    def row(a):
        return {"version": a.version, "provider": a.provider,
                "source_summary_version": a.source_summary_version,
                "created_at": a.created_at}
    return {"summary": [row(a) for a in rows if a.kind == "summary"],
            "analysis": [row(a) for a in rows if a.kind == "analysis"]}


@router.post("/{meeting_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_meeting(
    meeting_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Re-run summary + analysis from the existing transcript as NEW versions."""
    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id,
                              Meeting.user_id == current_user.id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not meeting.transcript_path or not Path(meeting.transcript_path).exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No transcript to regenerate from")
    from ...processing.queue import processing_queue
    if processing_queue.is_processing(meeting_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Meeting is already processing")
    meeting.status = "processing"
    meeting.progress = 80
    # Starting again must drop the PREVIOUS run's failure, or the cabinet shows
    # "Interrupted: the server stopped…" on a meeting that is actively running.
    meeting.error_message = None
    await db.commit()
    await processing_queue.add_meeting(meeting_id, regenerate=True)
    return {"status": "queued", "meeting_id": meeting_id}


def _ffmpeg():
    """Path to the bundled ffmpeg (the same binary the pipeline uses)."""
    return _media_module().paths.ffmpeg_executable()


def _media_module():
    import sys as _sys
    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))
    from desktop.app.backend import media
    return media


def _speakers_module():
    import sys as _sys
    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))
    from desktop.app.backend import speakers
    return speakers


async def _meeting_transcript(db, meeting_id, user_id):
    meeting = (await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == user_id))).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if not meeting.transcript_path or not Path(meeting.transcript_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transcript")
    return meeting, Path(meeting.transcript_path).read_text(encoding="utf-8", errors="replace")


@router.post("/{meeting_id}/cancel")
async def cancel_meeting(meeting_id: int,
                         current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """Stop a queued or running meeting.

    The desktop client can cancel a job; the cabinet had no way to stop anything,
    so a wrong engine or a three-hour upload held a worker to the very end.
    """
    result = await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == current_user.id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting not found")
    from ...processing.queue import processing_queue
    outcome = await processing_queue.cancel_meeting(meeting_id)
    if outcome == "idle":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Meeting is neither queued nor processing")
    return {"id": meeting_id, "cancelled": outcome}


@router.post("/{meeting_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_meeting(meeting_id: int,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Queue a recording that was uploaded with ``process=false``.

    That is the "process the whole file" answer to the trim window: the upload
    was deliberately held back, and this puts the original in as one meeting.
    """
    result = await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == current_user.id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting not found")
    if meeting.status == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Meeting is already being processed")
    # Same as regenerate: a retry after a failure must not keep showing the
    # previous run's error while the new one is in flight.
    if meeting.error_message:
        meeting.error_message = None
        await db.commit()
    from ...processing.queue import processing_queue
    await processing_queue.add_meeting(meeting.id)
    return {"id": meeting.id, "queued": True}


@router.get("/{meeting_id}/waveform")
async def meeting_waveform(meeting_id: int, buckets: int = Query(800, ge=50, le=4000),
                           current_user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """Amplitude envelope of the recording, for the cabinet's trim view.

    The media itself is never sent to the browser - a meeting can be gigabytes,
    and drawing a waveform client-side would mean downloading and decoding all of
    it. ffmpeg decodes to 8 kHz mono PCM here and only ``buckets`` peaks travel.
    """
    result = await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == current_user.id))
    meeting = result.scalar_one_or_none()
    if not meeting or not meeting.video_path or not Path(meeting.video_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting media not found")

    media = _media_module()
    duration = media.probe_duration(meeting.video_path)
    proc = await asyncio.create_subprocess_exec(
        str(_ffmpeg()), "-v", "error", "-i", str(meeting.video_path),
        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    raw, err = await proc.communicate()
    if proc.returncode != 0 or not raw:
        # Dumping ffmpeg's stderr tail put a multi-line English diagnostic into the
        # UI, sliced mid-word ("…ata found when processing input"). The user gets one
        # clear sentence; the detail goes to the server log where it belongs.
        print(f"[waveform] ffmpeg failed for meeting {meeting_id}: "
              f"{(err or b'').decode('utf-8', 'replace')[-500:]}", file=sys.stderr,
              flush=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read the recording - the file is damaged or its "
                   "format is not supported")

    import array
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) - (len(raw) % 2)])
    total = len(samples)
    if not total:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The recording has no audio track")
    step = max(1, total // buckets)
    peaks = []
    for i in range(0, total, step):
        window = samples[i:i + step]
        peaks.append(max(abs(min(window)), abs(max(window))) / 32768.0)
    peaks = peaks[:buckets]
    # Scale to the file's own loudest peak. A quiet recording (a distant mic, a
    # normalised-down export) would otherwise draw as a flat line and the user
    # could not see where the speech is - which is the whole point of the view.
    loudest = max(peaks) if peaks else 0.0
    if loudest > 0:
        peaks = [round(v / loudest, 4) for v in peaks]
    return {"duration": duration or (total / 8000.0), "peaks": peaks,
            "peak_level": round(loudest, 4)}


@router.post("/{meeting_id}/segments", status_code=status.HTTP_201_CREATED)
async def cut_segments(meeting_id: int, data: SegmentCut,
                       current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Cut one recording into per-meeting segments and queue each of them.

    Mirrors the desktop Trim dialog: every segment becomes a meeting of its own
    with its own transcript, summary and analysis. The source meeting is left
    alone - it is the user's to keep or delete.
    """
    result = await db.execute(select(Meeting).where(
        Meeting.id == meeting_id, Meeting.user_id == current_user.id))
    meeting = result.scalar_one_or_none()
    if not meeting or not meeting.video_path or not Path(meeting.video_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Meeting media not found")
    if not data.segments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No segments given")

    media = _media_module()
    duration = media.probe_duration(meeting.video_path) or 0.0
    for seg in data.segments:
        if seg.end <= seg.start or seg.start < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Invalid segment {seg.start}-{seg.end}")
        if duration and seg.end > duration + 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Segment {seg.start}-{seg.end} is past the "
                                       f"end of the recording ({duration:.1f}s)")

    from ...processing.queue import processing_queue
    created = []
    for seg in data.segments:
        name = media.segment_filename(meeting.original_filename, seg.start, seg.end)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dst = UPLOAD_DIR / f"{current_user.id}_{stamp}_{uuid.uuid4().hex[:12]}_{name}"
        try:
            await asyncio.to_thread(media.cut_segment, meeting.video_path, dst,
                                    seg.start, seg.end)
        except Exception as e:  # noqa: BLE001 - report the real ffmpeg message
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Could not cut {seg.start}-{seg.end}: {e}")
        child = Meeting(user_id=current_user.id, filename=dst.name,
                        original_filename=name, status="uploaded",
                        video_path=str(dst), file_size=dst.stat().st_size,
                        uploaded_at=datetime.utcnow())
        db.add(child)
        await db.commit()
        await db.refresh(child)
        await processing_queue.add_meeting(child.id)
        created.append({"id": child.id, "filename": name,
                        "start": seg.start, "end": seg.end})
    return {"source_id": meeting.id, "created": created}


@router.get("/{meeting_id}/transcript")
async def get_transcript(meeting_id: int,
                         current_user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    """The transcript as text, so the cabinet can show and edit it."""
    meeting, text = await _meeting_transcript(db, meeting_id, current_user.id)
    return {"id": meeting.id, "text": text}


@router.put("/{meeting_id}/transcript")
async def save_transcript(meeting_id: int, data: TranscriptUpdate,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Save a hand-corrected transcript over the recognised one.

    The desktop's transcript pane is editable and Regenerate then works from the
    corrected text; the cabinet could only ever download it. Regenerate here
    reads the same file, so a correction feeds the next summary and analysis.
    """
    meeting, _ = await _meeting_transcript(db, meeting_id, current_user.id)
    text = data.text or ""
    if not text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Refusing to save an empty transcript")
    Path(meeting.transcript_path).write_text(text, encoding="utf-8")
    return {"id": meeting.id, "saved": len(text)}


@router.get("/{meeting_id}/speakers")
async def get_speakers(meeting_id: int, current_user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Diarisation speakers + per-speaker stats (empty for a non-diarised transcript)."""
    _, text = await _meeting_transcript(db, meeting_id, current_user.id)
    sp = _speakers_module()
    return {"speakers": sp.extract_speakers(text),
            "stats": sp.speaker_stats(sp.parse_utterances(text))}


@router.post("/{meeting_id}/speakers/rename")
async def rename_speakers(meeting_id: int, data: SpeakerRename,
                          current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """Rename diarisation labels to display names, rewriting the transcript in place."""
    meeting, text = await _meeting_transcript(db, meeting_id, current_user.id)
    sp = _speakers_module()
    new_text = sp.rename_in_transcript(text, data.name_map)
    Path(meeting.transcript_path).write_text(new_text, encoding="utf-8")
    return {"speakers": sp.extract_speakers(new_text)}


@router.get("/{meeting_id}/export-by-speaker")
async def export_by_speaker(meeting_id: int, current_user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    """One text file per speaker, returned as a zip."""
    import zipfile
    import tempfile
    meeting, text = await _meeting_transcript(db, meeting_id, current_user.id)
    sp = _speakers_module()
    if not sp.extract_speakers(text):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Transcript has no diarisation speakers")
    stem = Path(meeting.original_filename).stem
    tmp = Path(tempfile.mkdtemp())
    files = sp.export_by_speaker(text, tmp, stem)
    zip_path = tmp / f"{stem}_by_speaker.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, Path(f).name)
    return FileResponse(path=str(zip_path), filename=zip_path.name,
                        media_type="application/zip")
