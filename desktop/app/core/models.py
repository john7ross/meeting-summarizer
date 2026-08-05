"""Data model for jobs and history entries, plus backend-stage mapping.

Statuses have a single authoritative owner (the worker), and the UI reads
labels from here, so there is exactly one place that decides what "the current
status" is for a given history id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    ANALYZING = "analyzing"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


# Backend `stage` strings (from processor.py / engines) -> coarse status.
STAGE_TO_STATUS = {
    "status.extracting": JobStatus.EXTRACTING,
    "status.transcribing": JobStatus.TRANSCRIBING,
    "status.complete": JobStatus.TRANSCRIBING,  # transcription finished; worker advances next
    "status.error": JobStatus.ERROR,
}

# Main status labels shown in the UI (ru / en).
STATUS_LABELS = {
    "ru": {
        JobStatus.QUEUED: "В очереди",
        JobStatus.EXTRACTING: "Извлечение аудио…",
        JobStatus.TRANSCRIBING: "Транскрибация…",
        JobStatus.SUMMARIZING: "Создание саммари…",
        JobStatus.ANALYZING: "Расширенный анализ…",
        JobStatus.DONE: "Готово",
        JobStatus.ERROR: "Ошибка",
        JobStatus.CANCELLED: "Отменено",
    },
    "en": {
        JobStatus.QUEUED: "Queued",
        JobStatus.EXTRACTING: "Extracting audio…",
        JobStatus.TRANSCRIBING: "Transcribing…",
        JobStatus.SUMMARIZING: "Generating summary…",
        JobStatus.ANALYZING: "Deep analysis…",
        JobStatus.DONE: "Done",
        JobStatus.ERROR: "Error",
        JobStatus.CANCELLED: "Cancelled",
    },
}


def stage_to_status(stage: str) -> Optional[JobStatus]:
    return STAGE_TO_STATUS.get(stage)


def main_label(status: JobStatus, language: str = "ru") -> str:
    table = STATUS_LABELS.get(language, STATUS_LABELS["ru"])
    return table.get(status, str(status.value))


@dataclass
class SummaryVersion:
    version: int
    path: str
    provider: str = ""
    model: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {"version": self.version, "path": self.path,
                "provider": self.provider, "model": self.model,
                "createdAt": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "SummaryVersion":
        return cls(version=int(d.get("version", 0)), path=d.get("path", ""),
                   provider=d.get("provider", ""), model=d.get("model", ""),
                   created_at=d.get("createdAt", ""))


@dataclass
class AnalysisVersion(SummaryVersion):
    """Analysis version. Adds ``source_summary_version`` to record which
    summary version this analysis was derived from, so the two never silently
    drift apart (e.g. a regenerated summary without a paired analysis). A value
    of 0 means "unknown / not linked" (legacy entries)."""
    source_summary_version: int = 0

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["sourceSummaryVersion"] = self.source_summary_version
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisVersion":
        base = SummaryVersion.from_dict(d)
        return cls(version=base.version, path=base.path, provider=base.provider,
                   model=base.model, created_at=base.created_at,
                   source_summary_version=int(d.get("sourceSummaryVersion", 0)))


@dataclass
class HistoryEntry:
    id: int
    video_path: str
    video_name: str
    process_id: Optional[float] = None
    duration: str = ""
    size: str = ""
    processed_at: str = ""
    transcript_path: Optional[str] = None
    summary_path: Optional[str] = None  # mirror of latest summary (Electron compat)
    status: str = JobStatus.QUEUED.value
    # Why the run ended the way it did. Without it a failed meeting showed
    # "Error / 0%" for ever with nothing to act on - the reason lived only in the
    # running process and died with it.
    error: str = ""
    project: str = ""  # user-assigned project id, for RAG scoping
    folder: str = ""   # artifact sub-folder name (sanitized file name, not the id)
    summary_versions: list = field(default_factory=list)
    analysis_versions: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # preserve unknown keys round-trip

    # JSON keys that map to explicit fields (everything else goes to `extra`).
    _KNOWN = {
        "id", "processId", "videoPath", "videoName", "duration", "size",
        "processedAt", "transcriptPath", "summaryPath", "status", "error",
        "project", "folder",
        "summaryVersions", "analysisVersions",
    }

    def to_dict(self) -> dict:
        data: dict[str, Any] = dict(self.extra)  # start with preserved unknowns
        data.update({
            "id": self.id,
            "processId": self.process_id,
            "videoPath": self.video_path,
            "videoName": self.video_name,
            "duration": self.duration,
            "size": self.size,
            "processedAt": self.processed_at,
            "transcriptPath": self.transcript_path,
            "summaryPath": self.summary_path,
            "status": self.status,
            "error": self.error,
            "project": self.project,
            "folder": self.folder,
            "summaryVersions": [v.to_dict() for v in self.summary_versions],
            "analysisVersions": [v.to_dict() for v in self.analysis_versions],
        })
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        extra = {k: v for k, v in d.items() if k not in cls._KNOWN}
        return cls(
            id=int(d["id"]),
            video_path=d.get("videoPath", ""),
            video_name=d.get("videoName", ""),
            process_id=d.get("processId"),
            duration=d.get("duration", ""),
            size=d.get("size", ""),
            processed_at=d.get("processedAt", ""),
            transcript_path=d.get("transcriptPath"),
            summary_path=d.get("summaryPath"),
            status=d.get("status", JobStatus.QUEUED.value),
            error=str(d.get("error", "") or ""),
            project=d.get("project", ""),
            folder=d.get("folder", ""),
            summary_versions=[SummaryVersion.from_dict(x)
                              for x in d.get("summaryVersions", [])],
            analysis_versions=[AnalysisVersion.from_dict(x)
                               for x in d.get("analysisVersions", [])],
            extra=extra,
        )
