#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLAlchemy models for the cabinet's SQLite database.

There is no PostgreSQL here despite what this docstring used to claim: the
cabinet deliberately ships with zero external database server (see
``database/db.py``), and the architecture doc reads this line verbatim.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class User(Base):
    """Модель пользователя"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user")  # user, admin
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    # Relationships
    meetings = relationship("Meeting", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', role='{self.role}')>"


class Meeting(Base):
    """Модель встречи"""
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    status = Column(String(20), default="uploading", index=True)  # uploading, processing, completed, failed

    # Пути к файлам
    video_path = Column(String(500), nullable=True)
    source_url = Column(String(1000), nullable=True)  # set when created from a URL (Feature 2)
    transcript_path = Column(String(500), nullable=True)
    summary_path = Column(String(500), nullable=True)
    analysis_path = Column(String(500), nullable=True)

    # Метаданные
    duration = Column(String(50), nullable=True)
    file_size = Column(Integer, nullable=True)  # в байтах
    project = Column(String(100), nullable=True, index=True)  # group + RAG/contextual-memory scope

    # Статистика обработки
    processing_time = Column(Integer, nullable=True)  # в секундах
    error_message = Column(Text, nullable=True)

    # Живой прогресс (персистится, чтобы кабинет показывал статус после перезагрузки)
    progress = Column(Integer, default=0)           # 0..100
    stage = Column(String(40), nullable=True)       # status.transcribing / summarizing / analyzing
    eta_seconds = Column(Integer, nullable=True)     # оценка оставшегося времени, сек

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    uploaded_at = Column(DateTime, nullable=True)
    processing_started_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="meetings")

    def __repr__(self):
        return f"<Meeting(id={self.id}, filename='{self.filename}', status='{self.status}')>"


class UserSettings(Base):
    """Настройки пользователя"""
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Настройки в JSON формате
    settings_json = Column(Text, nullable=False, default="{}")

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="settings")

    def __repr__(self):
        return f"<UserSettings(user_id={self.user_id})>"


class ServerSettings(Base):
    """Installation-wide settings, owned by administrators.

    Single row (id=1). These are NOT per-user: the worker count is load management
    for the whole machine, so one user must not be able to raise it for everybody,
    and the admin's choice has to survive a restart - it used to live only in the
    queue object's memory and reverted to the auto-detected value on every boot.
    """
    __tablename__ = "server_settings"

    id = Column(Integer, primary_key=True, index=True)
    settings_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<ServerSettings(updated_at={self.updated_at})>"


class ProcessingLog(Base):
    """Лог обработки для отладки"""
    __tablename__ = "processing_logs"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    log_level = Column(String(10), default="INFO")  # DEBUG, INFO, WARNING, ERROR
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<ProcessingLog(meeting_id={self.meeting_id}, level='{self.log_level}')>"


class Artifact(Base):
    """One version of a generated artifact (summary/analysis) for a meeting.

    Each Regenerate creates a NEW row (v2, v3, …) instead of overwriting, mirroring
    the desktop's version history. ``Meeting.summary_path``/``analysis_path`` keep
    pointing at the LATEST version for quick download/compat."""
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False, index=True)   # summary | analysis
    version = Column(Integer, nullable=False)
    path = Column(String(500), nullable=False)
    provider = Column(String(40), nullable=True)
    # For an analysis: which summary version it was derived from (drift tracking).
    source_summary_version = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<Artifact(meeting={self.meeting_id}, {self.kind} v{self.version})>"


class UserTemplate(Base):
    """A user's saved prompt template (the built-in library is served read-only)."""
    __tablename__ = "user_templates"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    prompt = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<UserTemplate(user={self.user_id}, name='{self.name}')>"
