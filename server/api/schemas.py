#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pydantic schemas для валидации запросов/ответов
"""
from pathlib import Path
import email_validator
from pydantic import BaseModel, EmailStr, Field, computed_field
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

# This cabinet never sends mail - there is no smtplib anywhere in the project -
# so the address is an account identifier and a uniqueness key, nothing more.
# email-validator rejects special-use domains by default to stop software from
# mailing into the void, which buys us nothing and blocks the addresses a LAN
# deployment actually uses: user@nas.local, user@corp.internal, or the RFC 8375
# home network name user@host.home.arpa. Registration answered 422 "special-use
# or reserved name" on all of them, which reads like a typo rather than policy.
# Syntax is still fully validated, INCLUDING the requirement that the domain
# carry a dot - so a bare "admin@localhost" is still refused as the typo it
# usually is. 'invalid' stays blocked too: RFC 2606 reserves it to mean exactly
# that, so it catches placeholder input instead of naming a real host.
for _reserved in ("local", "localhost", "test", "arpa", "onion"):
    if _reserved in email_validator.SPECIAL_USE_DOMAIN_NAMES:
        email_validator.SPECIAL_USE_DOMAIN_NAMES.remove(_reserved)


def _has_content(path: Optional[str]) -> bool:
    """True only if the file exists AND is not empty.

    A path alone is not content: a run that recognised no speech still records a
    transcript path, and the cabinet then offered a "Download transcript" button
    that handed the user a zero-byte file.
    """
    if not path:
        return False
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 0
    except OSError:
        return False


# ============================================================================
# Auth Schemas
# ============================================================================

class UserRegister(BaseModel):
    """Схема регистрации пользователя"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    """Схема входа пользователя"""
    username: str
    password: str


class Token(BaseModel):
    """Схема токена"""
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Схема ответа с данными пользователя"""
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================================
# Meeting Schemas
# ============================================================================

class MeetingCreate(BaseModel):
    """Схема создания встречи"""
    filename: str
    original_filename: str


class MeetingFromUrl(BaseModel):
    """Create a meeting from a video URL (YouTube / file server / etc.).

    The media is downloaded during processing and then handled exactly like an
    uploaded video."""
    url: str = Field(..., min_length=8, max_length=1000)
    project: Optional[str] = None


class MeetingUpdate(BaseModel):
    """Схема обновления встречи"""
    # ``project`` groups meetings and scopes RAG / contextual memory. The column
    # existed and the RAG routes filtered on it, but nothing could ever set it,
    # so in the cabinet every meeting stayed project-less.
    project: Optional[str] = None
    status: Optional[str] = None
    video_path: Optional[str] = None
    transcript_path: Optional[str] = None
    summary_path: Optional[str] = None
    analysis_path: Optional[str] = None
    project: Optional[str] = None
    duration: Optional[str] = None
    file_size: Optional[int] = None
    processing_time: Optional[int] = None
    error_message: Optional[str] = None


class MeetingResponse(BaseModel):
    """Схема ответа со встречей"""
    id: int
    user_id: int
    filename: str
    original_filename: str
    status: str
    video_path: Optional[str]
    source_url: Optional[str] = None
    transcript_path: Optional[str]
    summary_path: Optional[str]
    analysis_path: Optional[str]
    duration: Optional[str]
    # Without this the cabinet could never SHOW a project even once it could set
    # one - the column is what scopes RAG and contextual memory.
    project: Optional[str] = None
    file_size: Optional[int]
    processing_time: Optional[int]
    error_message: Optional[str]
    progress: Optional[int] = 0
    stage: Optional[str] = None
    eta_seconds: Optional[int] = None
    created_at: datetime
    uploaded_at: Optional[datetime]
    processing_started_at: Optional[datetime]
    processed_at: Optional[datetime]

    # Whether each artifact actually HAS content, so the cabinet can offer a
    # download only when there is something to download.
    @computed_field
    @property
    def has_source(self) -> bool:
        return _has_content(self.video_path)

    @computed_field
    @property
    def has_transcript(self) -> bool:
        return _has_content(self.transcript_path)

    @computed_field
    @property
    def has_summary(self) -> bool:
        return _has_content(self.summary_path)

    @computed_field
    @property
    def has_analysis(self) -> bool:
        return _has_content(self.analysis_path)

    class Config:
        from_attributes = True


class MeetingListResponse(BaseModel):
    """Схема списка встреч"""
    total: int
    meetings: List[MeetingResponse]


class MeetingStatusResponse(BaseModel):
    """Лёгкий статус для поллинга кабинета (без путей к файлам)."""
    id: int
    status: str
    progress: int = 0
    stage: Optional[str] = None
    eta_seconds: Optional[int] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================================================
# Settings Schemas
# ============================================================================

class TranscriptUpdate(BaseModel):
    """Replace a meeting's transcript with a hand-corrected version."""
    text: str


class SpeakerRename(BaseModel):
    """Map diarisation labels to display names, e.g. {"SPEAKER_00": "Иван"}."""
    name_map: Dict[str, str]


class Segment(BaseModel):
    """One [start, end) span of a recording, in seconds."""
    start: float
    end: float


class SegmentCut(BaseModel):
    """Split one uploaded recording into per-meeting segments."""
    segments: List[Segment]


class ObsidianExport(BaseModel):
    """Which notes to write, and which artifact versions they come from.

    ``0`` means "the newest"; any other number must exist or the export fails
    rather than quietly writing a different version than the one on screen.
    """
    kinds: Optional[List[Literal["summary", "analysis", "raw"]]] = None
    summary_version: int = 0
    analysis_version: int = 0


class TemplateCreate(BaseModel):
    """Create/update a user prompt template."""
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = ""


class TemplateResponse(BaseModel):
    id: int
    name: str
    prompt: str

    class Config:
        from_attributes = True


class SettingsData(BaseModel):
    """Partial, typed user settings (send any subset — omitted fields are left as-is).

    Small stable enums are validated (device/language/diarization); engine, model and
    provider are free strings validated by the client against ``/api/engines``."""
    transcriptionEngine: Optional[str] = None
    whisperModel: Optional[str] = None
    transcriptionLanguage: Optional[Literal["ru", "en"]] = None
    outputLanguage: Optional[Literal["auto", "ru", "en"]] = None  # summary/analysis language
    whisperDevice: Optional[Literal["auto", "cuda", "cpu"]] = None
    transcriptionHint: Optional[str] = None
    diarizationBackend: Optional[Literal["sherpa", "pyannote", "off"]] = None
    hfToken: Optional[str] = None
    aiProvider: Optional[str] = None
    aiModel: Optional[str] = None
    apiKey: Optional[str] = None
    localEndpoint: Optional[str] = None
    analysisSource: Optional[Literal["transcript", "summary"]] = None
    ragCatalogMode: Optional[Literal["isolated", "shared"]] = None
    ragSharedCatalogKey: Optional[str] = None
    aiTimeout: Optional[int] = None          # per-request seconds (0=default 600); raise for long meetings
    disableReasoning: Optional[bool] = None  # skip a reasoning model's <think> phase (faster)
    aiRetries: Optional[int] = None          # retry local-model connection failures (watchdog restart)
    aiRetryDelay: Optional[int] = None       # base seconds between retries (escalates)
    chunkChars: Optional[int] = None         # map-reduce threshold (0=default); raise for big-context models
    chunkingEnabled: Optional[bool] = None   # off => always send whole transcript (best quality; may exceed context)

    gpuHandoff: Optional[bool] = None        # stop the local LLM to free VRAM for GPU transcription
    llamaPort: Optional[int] = None          # local LLM port to stop during hand-off (default 8080)
    youtubeCookiesBrowser: Optional[str] = None  # cookie source for auth-gated URLs: auto|off|chrome|…
    googleSheetsIntegration: Optional[bool] = None   # auto-append a row to a Google Sheet on finish
    googleSheetsUrl: Optional[str] = None            # Apps Script /exec webhook URL
    googleSheetsToken: Optional[str] = None          # optional SHARED_TOKEN for that webhook
    useContextualMemory: Optional[bool] = None       # inject prior SAME-PROJECT summaries into the prompt
    # A key missing HERE is silently dropped: model_dump(exclude_unset=True) only
    # keeps declared fields, so the UI could send projectId all day and the save
    # would discard it while both DEFAULT_SETTINGS and the form looked correct.
    projectId: Optional[str] = None                  # default project for grouping/contextual memory
    useSpeakerPrompt: Optional[bool] = None          # speaker-aware prompt variant when diarised
    agentCommand: Optional[str] = None               # provider="agent": command template
    agentCwd: Optional[str] = None                   # provider="agent": working directory
    ragEmbeddingBackend: Optional[str] = None        # sentence-transformers|openai|local
    ragEmbeddingModel: Optional[str] = None
    obsidianIntegration: Optional[bool] = None       # enable the Obsidian export button
    obsidianVaultPath: Optional[str] = None          # vault directory ON THE SERVER
    createPeopleNotes: Optional[bool] = None
    createTopicNotes: Optional[bool] = None
    createDataviewQueries: Optional[bool] = None
    updateMeetingIndex: Optional[bool] = None
    enableMarkdownExport: Optional[bool] = None
    prompt: Optional[str] = None
    extractActionItems: Optional[bool] = None
    analyzeSentiment: Optional[bool] = None
    categorizeAutomatically: Optional[bool] = None
    generateFollowupQuestions: Optional[bool] = None
    generateFormalProtocol: Optional[bool] = None
    advancedSettings: Optional[Dict[str, Any]] = None


# Backwards-compatible alias: PUT /api/settings accepts a partial SettingsData.
SettingsUpdate = SettingsData


class SettingsResponse(BaseModel):
    """Structured settings (defaults merged with the user's saved values)."""
    user_id: int
    settings: Dict[str, Any]
    updated_at: Optional[datetime] = None


# ============================================================================
# Processing Log Schemas
# ============================================================================

class ProcessingLogCreate(BaseModel):
    """Схема создания лога"""
    meeting_id: int
    log_level: str = "INFO"
    message: str


class ProcessingLogResponse(BaseModel):
    """Схема ответа с логом"""
    id: int
    meeting_id: int
    log_level: str
    message: str
    created_at: datetime

    class Config:
        from_attributes = True
