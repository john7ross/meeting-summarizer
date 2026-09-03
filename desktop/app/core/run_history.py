"""Processing journal: one record per RUN, kept apart from the meeting archive.

``history.json`` is the ARCHIVE — one entry per meeting, holding its artifacts,
and it is what the queue table is rebuilt from. Removing a meeting from the queue
deletes that entry, which used to take the only trace of the processing with it:
after a cleanup there was no way to tell when a file was processed, how long it
took, which stages ran or why a run failed.

This module keeps the other half — the LOG of runs. It is append-only, keyed by
its own ``runId``, and nothing in the queue can delete from it:

    run  =  one press of Process / Regenerate for one meeting
            kind: full | summary | analysis | summary+analysis
            when it started and finished, how long it took,
            every status transition with its timestamp,
            every finished stage with its duration,
            the artifacts it produced, and the error if it failed

A record is written when the run starts and rewritten on every status change, so
a crash mid-run still leaves evidence. Runs left without a finish (the app was
closed or killed) are stamped ``interrupted`` on the next start —
:func:`RunHistoryStore.mark_interrupted`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .. import paths
from .atomic_io import atomic_write_json, file_lock

# Status of a run that never reported an end (app closed/killed mid-run). Not a
# JobStatus: the pipeline never produces it, the journal does.
INTERRUPTED = "interrupted"

# What a run was asked to do. 'full' is the complete pipeline (transcription
# included); the other three are regenerations, which never transcribe again.
RUN_KINDS = ("full", "summary", "analysis", "summary+analysis")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class RunHistoryStore:
    """Read/append the processing journal (``config/processing_history.json``).

    Every write is a locked read-modify-write over the whole list, exactly like
    :class:`~.history.HistoryStore` — the file is small, human-readable, and may
    be open in a second app instance.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else paths.PROCESSING_HISTORY_FILE

    # ---- raw IO ------------------------------------------------------
    def _read_raw(self) -> list:
        if not self.path.exists():
            return []
        for attempt in range(8):
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                if attempt == 7:
                    return []
                time.sleep(0.03 * (attempt + 1))
        return []

    # ---- API ---------------------------------------------------------
    def load(self) -> list[dict]:
        """Every run, oldest first (the order they were appended in)."""
        return [r for r in self._read_raw() if isinstance(r, dict)]

    def new_run(self, *, entry_id: int, video_name: str = "", video_path: str = "",
                kind: str = "full", provider: str = "", model: str = "",
                engine: str = "", source: str = "") -> dict:
        """Create and persist a started run; returns the record to keep updating.

        source is the meeting'''s intake channel ('''file''' / '''live'''). A live
        meeting runs the same stages as a regenerated file, so without it the
        journal cannot tell the two apart afterwards — which is precisely what
        someone reading the journal is trying to find out.
        """
        record = {
            "runId": f"{int(entry_id)}-{int(time.time() * 1000)}",
            "entryId": int(entry_id),
            "videoName": video_name,
            "videoPath": video_path,
            "kind": kind if kind in RUN_KINDS else "full",
            "startedAt": _now(),
            "finishedAt": "",
            "durationSec": 0.0,
            "status": "",
            "error": "",
            "provider": provider,
            "model": model,
            "engine": engine,
            "source": source or "file",
            "events": [],
            "stages": [],
            "artifacts": [],
        }
        self.upsert(record)
        return record

    def upsert(self, record: dict) -> None:
        """Insert or replace one run by its ``runId``."""
        run_id = str(record.get("runId") or "")
        if not run_id:
            return
        with file_lock(self.path):
            raw = self._read_raw()
            for i, item in enumerate(raw):
                if str(item.get("runId")) == run_id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            atomic_write_json(self.path, raw, lock=False)

    def mark_interrupted(self) -> int:
        """Close runs that never reported an end (crash / kill). Returns how many.

        Called at application start, where no run of a previous process can still
        be alive; without it those records would read as "in progress" for ever.
        """
        with file_lock(self.path):
            raw = self._read_raw()
            changed = 0
            for item in raw:
                if not isinstance(item, dict) or item.get("finishedAt"):
                    continue
                events = item.get("events") or []
                last = events[-1].get("at") if events else item.get("startedAt", "")
                item["finishedAt"] = last or item.get("startedAt", "")
                item["status"] = INTERRUPTED
                changed += 1
            if changed:
                atomic_write_json(self.path, raw, lock=False)
            return changed

    def clear(self) -> None:
        with file_lock(self.path):
            atomic_write_json(self.path, [], lock=False)


# ---- record helpers (pure, so the pipeline stays free of journal details) ----
def add_event(record: dict, status: str) -> None:
    """Record a status transition with the time it happened."""
    record.setdefault("events", []).append({"at": _now(), "status": str(status)})
    record["status"] = str(status)


def add_stage(record: dict, label: str, seconds: float) -> None:
    """Record a finished stage (audio extraction, a chunk, an analysis feature)."""
    record.setdefault("stages", []).append(
        {"at": _now(), "label": str(label), "seconds": float(seconds)})


def add_artifact(record: dict, kind: str, version: int) -> None:
    record.setdefault("artifacts", []).append({"kind": str(kind), "version": int(version)})


def finish(record: dict, status: str, error: str = "", duration_sec: float = 0.0) -> None:
    record["finishedAt"] = _now()
    record["status"] = str(status)
    record["durationSec"] = round(float(duration_sec), 3)
    if error:
        record["error"] = str(error)[:2000]
