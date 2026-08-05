"""History store: the single source of truth keyed by a unique file id.

Adding a file appends an entry and returns its unique id (a millisecond
timestamp, matching the existing Electron data); the same file added twice gets
two ids. Transcription, summary versions and analysis versions all attach to
that id. Writes are atomic and unknown JSON keys are preserved, so the file
stays interchangeable with the Electron front-end.

Artifact naming follows the input file name, with versions ``v2, v3, ...``
appended for the 2nd and later summary/analysis (the transcript is single):

    <stem>_raw.txt             transcript (raw, single — written by processor.py)
    <stem>_summary.txt         summary v1        <stem>_summary_v2.txt  ...
    <stem>_analysis.json       analysis v1       <stem>_analysis_v2.json ...
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from .. import paths
from .atomic_io import atomic_write_json, file_lock
from .models import AnalysisVersion, HistoryEntry, JobStatus, SummaryVersion

_BAD_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_folder(name: str) -> str:
    """A safe artifact-folder name from a file name: drop the extension and any
    characters Windows forbids, trim trailing dots/spaces. '' -> 'meeting'."""
    stem = Path(name).stem or name
    cleaned = _BAD_FS_CHARS.sub("", stem).strip().rstrip(". ")
    return cleaned or "meeting"


def versioned_filename(stem: str, kind: str, version: int, ext: str) -> str:
    """Build an artifact file name. ``kind`` is '', 'summary' or 'analysis'."""
    base = stem if not kind else f"{stem}_{kind}"
    if version and version > 1:
        base = f"{base}_v{version}"
    if ext and not ext.startswith("."):
        ext = "." + ext
    return f"{base}{ext}"


class HistoryStore:
    def __init__(self, path: Optional[Path] = None,
                 transcripts_root: Optional[Path] = None):
        self.path = Path(path) if path else paths.HISTORY_FILE
        self.transcripts_root = (Path(transcripts_root) if transcripts_root
                                 else paths.TRANSCRIPTS_DIR)

    # ---- raw list IO -------------------------------------------------
    def _read_raw(self) -> list:
        if not self.path.exists():
            return []
        # Windows may briefly deny an open while another process atomically
        # replaces the file.  Treating that transient state as an empty history
        # makes the caller overwrite valid data, so retry before giving up.
        data = None
        for attempt in range(8):
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                break
            except (json.JSONDecodeError, OSError):
                if attempt == 7:
                    return []
                time.sleep(0.03 * (attempt + 1))
        return data if isinstance(data, list) else []

    def _write_raw(self, items: list) -> None:
        # Caller holds file_lock across the complete read-modify-write.
        atomic_write_json(self.path, items, lock=False)

    # ---- entry-level API ---------------------------------------------
    def load(self) -> list[HistoryEntry]:
        return [HistoryEntry.from_dict(d) for d in self._read_raw()]

    def get(self, entry_id: int) -> Optional[HistoryEntry]:
        for entry in self.load():
            if entry.id == entry_id:
                return entry
        return None

    def remove(self, entry_id: int) -> bool:
        """Delete one meeting from the archive. True if it was there.

        Removing a row from the queue used to touch the table only, so every
        deleted meeting came back on the next start - the queue is rebuilt from
        this file. Produced files under the meeting's folder are left on disk;
        only the record goes.
        """
        with file_lock(self.path):
            raw = self._read_raw()
            kept = [item for item in raw if int(item.get("id", -1)) != int(entry_id)]
            if len(kept) == len(raw):
                return False
            self._write_raw(kept)
            return True

    def _save_entry(self, entry: HistoryEntry) -> None:
        with file_lock(self.path):
            raw = self._read_raw()
            updated = entry.to_dict()
            for i, item in enumerate(raw):
                if int(item.get("id", -1)) == entry.id:
                    raw[i] = updated
                    break
            else:
                raw.append(updated)
            self._write_raw(raw)

    def add(self, video_path, duration: str = "", size: str = "") -> int:
        existing = {int(d.get("id", 0)) for d in self._read_raw()}
        new_id = int(time.time() * 1000)
        while new_id in existing:
            new_id += 1
        video_path = str(video_path)
        video_name = os.path.basename(video_path)
        entry = HistoryEntry(
            id=new_id,
            video_path=video_path,
            video_name=video_name,
            process_id=new_id + round(time.time() % 1, 3),
            duration=duration,
            size=size,
            processed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            status=JobStatus.QUEUED.value,
            folder=self._unique_folder(video_name),
        )
        self._save_entry(entry)
        return new_id

    def _unique_folder(self, video_name: str) -> str:
        """Sanitized-file-name folder, made unique against folders already taken
        by OTHER entries (append ' (2)', ' (3)', …)."""
        base = sanitize_folder(video_name)
        taken = {d.get("folder") for d in self._read_raw() if d.get("folder")}
        # Also avoid clashing with a directory already on disk.
        candidate, n = base, 2
        while candidate in taken or (self.transcripts_root / candidate).exists():
            candidate = f"{base} ({n})"
            n += 1
        return candidate

    def job_dir(self, entry_id: int) -> Path:
        # Prefer the sanitized-file-name folder; fall back to the id for legacy
        # entries created before folder naming (their artifacts live under <id>/).
        entry = self.get(entry_id)
        name = (entry.folder if entry and entry.folder else str(entry_id))
        directory = self.transcripts_root / name
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def set_status(self, entry_id: int, status, error: str = "") -> None:
        """Persist the status and, for a failure, WHY it failed.

        The reason used to live only in the running process: after a restart the
        row said "error" and nothing else, so the user could not tell a quota
        problem from an unreachable endpoint or a broken file.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        entry.status = status.value if isinstance(status, JobStatus) else str(status)
        if error:
            entry.error = error.strip()[:600]
        elif entry.status not in ("error", "cancelled"):
            entry.error = ""          # a successful re-run clears the old reason
        self._save_entry(entry)

    def set_transcript(self, entry_id: int, path) -> None:
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        entry.transcript_path = str(path)
        self._save_entry(entry)

    def set_project(self, entry_id: int, project: str) -> None:
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        entry.project = (project or "").strip()
        self._save_entry(entry)

    def add_summary_version(self, entry_id: int, path, provider: str = "",
                            model: str = "") -> int:
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        version = len(entry.summary_versions) + 1
        entry.summary_versions.append(SummaryVersion(
            version=version, path=str(path), provider=provider, model=model,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
        entry.summary_path = str(path)  # mirror latest for Electron compat
        self._save_entry(entry)
        return version

    def add_analysis_version(self, entry_id: int, path, provider: str = "",
                             model: str = "",
                             source_summary_version: Optional[int] = None) -> int:
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        version = len(entry.analysis_versions) + 1
        # Default link: the latest summary version present at analysis time.
        if source_summary_version is None:
            source_summary_version = len(entry.summary_versions)
        entry.analysis_versions.append(AnalysisVersion(
            version=version, path=str(path), provider=provider, model=model,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            source_summary_version=source_summary_version))
        self._save_entry(entry)
        return version
