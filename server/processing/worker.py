#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Server-side processing worker — full pipeline (transcribe → summary → analysis).

Mirrors the desktop ``JobRunner`` but async and DB-backed. It REUSES the same
Qt-free backend adapters the desktop client uses (``desktop.app.backend`` —
``transcription`` / ``summarization`` / ``analysis``), which build the argv for
the verified backend CLIs and default to the embedded runtime
(``backend/python/python.exe``). So the web layer runs the heavy work in the
embedded Python via subprocess; the FastAPI process itself needs no torch.
"""
import os
import sys
import json
import asyncio
import signal
import atexit
from pathlib import Path
from datetime import datetime

SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'
if not SERVER_MODE:
    raise RuntimeError("worker should not be imported in desktop mode")

# Reuse the desktop's Qt-free backend adapters (no PySide import chain).
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO / "backend"))   # for gpu_handoff
from desktop.app.backend import transcription as T  # noqa: E402
from desktop.app.backend import summarization as S  # noqa: E402
from desktop.app.backend import analysis as A       # noqa: E402
from desktop.app.backend import media as _media     # noqa: E402

from sqlalchemy.ext.asyncio import AsyncSession       # noqa: E402
from sqlalchemy import select, func                    # noqa: E402

from ..database.models import Meeting, ProcessingLog, UserSettings, Artifact  # noqa: E402
from ..database.db import AsyncSessionLocal            # noqa: E402
from ..api.websocket import manager                    # noqa: E402
from ..runtime import backend_python                   # noqa: E402

TRANSCRIPTS_DIR = _REPO / "transcripts"
UPLOAD_DIR = _REPO / "uploads"
_PY = backend_python()
_URL_DL = _REPO / "backend" / "url_download.py"

# Defaults for a user who has saved nothing yet. This MUST be the very dict the
# settings API serves, not a copy: the copy that used to live here silently lost
# the five analysis feature flags, so every meeting of a user who had never saved
# settings finished "successfully" with no analysis artifact at all and every
# analysis export answered 404.
from ..api.routes.settings import DEFAULT_SETTINGS as _DEFAULTS  # noqa: E402



def _measure_duration(video_path, transcript_text: str) -> str:
    """Human-readable meeting length: ffprobe the media, else read the transcript."""
    try:
        if video_path and Path(video_path).exists():
            seconds = _media.probe_duration(video_path)
            if seconds > 0:
                return _media.format_timecode(seconds)
    except Exception:      # noqa: BLE001 - a missing length must not fail a run
        pass
    try:
        return _media.duration_from_transcript(transcript_text) or ""
    except Exception:      # noqa: BLE001
        return ""


def _analysis_source(settings, transcript, summary, summary_version=0):
    """Return the selected input path and truthful summary-version provenance."""
    if settings.get("analysisSource", "transcript") == "transcript" or not summary:
        return transcript, None
    return summary, summary_version or None


class ProcessingWorker:
    """Async processing worker for the web server."""

    class Cancelled(Exception):
        """The user stopped this meeting; not a processing failure."""

    def __init__(self):
        self.active_processes = []
        # Subprocesses keyed by meeting so ONE meeting can be stopped without
        # touching the other workers' runs.
        self.processes_by_meeting: dict = {}
        self.cancelled: set = set()
        atexit.register(self._cleanup_processes)
        signal.signal(signal.SIGTERM, lambda s, f: self._cleanup_processes())
        signal.signal(signal.SIGINT, lambda s, f: self._cleanup_processes())

    def _cleanup_processes(self):
        for proc in self.active_processes:
            try:
                proc.terminate()
            except Exception:
                pass
        self.active_processes.clear()

    def _track(self, meeting_id, process):
        self.active_processes.append(process)
        self.processes_by_meeting.setdefault(int(meeting_id), []).append(process)

    def _untrack(self, meeting_id, process):
        if process in self.active_processes:
            self.active_processes.remove(process)
        running = self.processes_by_meeting.get(int(meeting_id), [])
        if process in running:
            running.remove(process)
        if not running:
            self.processes_by_meeting.pop(int(meeting_id), None)

    def cancel(self, meeting_id: int) -> bool:
        """Stop this meeting's running subprocess. True if something was killed.

        The desktop can cancel a job; the cabinet could not stop anything, so a
        wrong engine or a three-hour upload occupied a worker to the end.
        """
        meeting_id = int(meeting_id)
        self.cancelled.add(meeting_id)
        killed = False
        for process in list(self.processes_by_meeting.get(meeting_id, [])):
            try:
                process.kill()
                killed = True
            except Exception:      # noqa: BLE001 - already gone
                pass
        return killed

    def is_cancelled(self, meeting_id: int) -> bool:
        return int(meeting_id) in self.cancelled

    # -- lifecycle ---------------------------------------------------------
    async def process_meeting(self, meeting_id: int, options: dict = None,
                              regenerate: bool = False):
        """Run the pipeline for one meeting, updating status + DB paths.

        ``regenerate`` skips transcription and re-runs summary + analysis from the
        existing transcript as NEW versions (like the desktop's Regenerate)."""
        options = options or {}
        async with AsyncSessionLocal() as db:
            try:
                meeting = (await db.execute(
                    select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
                if not meeting:
                    raise Exception(f"Meeting {meeting_id} not found")

                if self.is_cancelled(meeting_id):
                    # Cancelled while it was still waiting in the queue.
                    self.cancelled.discard(meeting_id)
                    meeting.status = "cancelled"
                    meeting.stage = "status.cancelled"
                    await db.commit()
                    await self._log(db, meeting_id, "INFO", "Cancelled before it started")
                    await manager.broadcast_status(meeting_id, "cancelled", "Cancelled")
                    return

                settings = await self._load_settings(db, meeting)
                settings.update(options)   # per-request overrides win

                meeting.status = "processing"
                meeting.processing_started_at = datetime.utcnow()
                meeting.progress = 0
                meeting.stage = "status.extracting"
                meeting.eta_seconds = None
                await db.commit()
                await self._log(db, meeting_id, "INFO", "Processing started")
                await manager.broadcast_status(meeting_id, "processing", "Processing started")

                out_dir = TRANSCRIPTS_DIR / str(meeting_id)
                out_dir.mkdir(parents=True, exist_ok=True)

                if regenerate:
                    transcript = Path(meeting.transcript_path or "")
                    if not transcript.exists():
                        raise Exception("No transcript to regenerate from")
                    await self._log(db, meeting.id, "INFO", "Regenerate: reusing transcript")
                else:
                    # From-URL meetings: fetch the video first, then treat it like
                    # any uploaded file for the rest of the pipeline.
                    if meeting.source_url and not (
                            meeting.video_path and Path(meeting.video_path).exists()):
                        await self._download_url(db, meeting, settings)
                    transcript = await self._transcribe(db, meeting, settings, out_dir)
                    meeting.transcript_path = str(transcript)
                    await db.commit()

                # The cabinet shows a meeting's length on the card and in the
                # detail modal, but nothing ever filled this column, so every
                # real meeting displayed none. Measure the media; fall back to
                # the transcript's last timestamp exactly like the exports do.
                if not (meeting.duration or "").strip():
                    meeting.duration = _measure_duration(
                        meeting.video_path,
                        Path(transcript).read_text(encoding="utf-8", errors="replace"))
                    await db.commit()

                if not Path(transcript).read_text(encoding="utf-8", errors="replace").strip():
                    raise Exception("No speech recognised — the transcript is empty "
                                    "(silent or non-speech media?)")

                summary, summary_ver = await self._summarize(
                    db, meeting, settings, transcript, out_dir)
                if summary:
                    meeting.summary_path = str(summary)
                    await db.commit()

                analysis = await self._analyze(
                    db, meeting, settings, transcript, summary, out_dir, summary_ver)
                if analysis:
                    meeting.analysis_path = str(analysis)
                    await db.commit()

                await self._maybe_export_gsheets(db, meeting, settings, summary, analysis)

                meeting.status = "completed"
                meeting.processed_at = datetime.utcnow()
                meeting.progress = 100
                meeting.stage = "status.complete"
                meeting.eta_seconds = 0
                if meeting.processing_started_at:
                    meeting.processing_time = int(
                        (meeting.processed_at - meeting.processing_started_at).total_seconds())
                await db.commit()
                await self._log(db, meeting_id, "INFO", "Processing completed")
                await manager.broadcast_completed(meeting_id)

            except self.Cancelled:
                self.cancelled.discard(meeting_id)
                meeting = (await db.execute(
                    select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
                if meeting:
                    meeting.status = "cancelled"
                    meeting.stage = "status.cancelled"
                    meeting.eta_seconds = 0
                    await db.commit()
                await self._log(db, meeting_id, "INFO", "Cancelled by the user")
                await manager.broadcast_status(meeting_id, "cancelled", "Cancelled")

            except Exception as e:
                self.cancelled.discard(meeting_id)
                meeting = (await db.execute(
                    select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
                if meeting:
                    meeting.status = "failed"
                    meeting.error_message = str(e)
                    await db.commit()
                await self._log(db, meeting_id, "ERROR", f"Processing failed: {e}")
                await manager.broadcast_error(meeting_id, str(e))
                raise

    async def _load_settings(self, db: AsyncSession, meeting: Meeting) -> dict:
        """User's saved settings (JSON) merged over the defaults."""
        merged = dict(_DEFAULTS)
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == meeting.user_id))).scalar_one_or_none()
        if row and row.settings_json:
            try:
                merged.update(json.loads(row.settings_json))
            except (ValueError, TypeError):
                pass
        return merged

    # -- stages ------------------------------------------------------------
    async def _download_url(self, db, meeting, settings) -> None:
        """Download the source URL's video into uploads/ and set it as video_path,
        streaming progress. Afterwards the pipeline treats it like an upload."""
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{meeting.user_id}_url_{meeting.id}"
        cookies = str(settings.get("youtubeCookiesBrowser", "auto") or "auto")
        cmd = [str(_PY), str(_URL_DL), meeting.source_url,
               "--out-dir", str(UPLOAD_DIR), "--name", name,
               "--cookies-from-browser", cookies]
        await self._log(db, meeting.id, "INFO", f"Downloading from URL: {meeting.source_url}")
        await self._set_progress(db, meeting, "status.downloading", 0, "download")

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._track(meeting.id, process)
        result, stderr_tail = None, []

        async def drain_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                msg = line.decode(errors="replace").strip()
                if msg:
                    stderr_tail.append(msg)
                    del stderr_tail[:-15]
        stderr_task = asyncio.create_task(drain_stderr())
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode(errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                ev = data.get("event")
                if ev == "progress":
                    # map 0..100 download → 0..30 overall (transcription starts ~40)
                    await self._set_progress(db, meeting, "status.downloading",
                                             int(data.get("percent", 0) * 0.3), "download")
                elif ev == "done":
                    result = data
                elif ev == "error":
                    raise Exception(f"Download failed: {data.get('message', '')[:300]}")
            await process.wait()
            await stderr_task
        finally:
            self._untrack(meeting.id, process)
        if self.is_cancelled(meeting.id):
            raise self.Cancelled()
        if process.returncode != 0 or not result or not result.get("path"):
            raise Exception(f"Download failed: {' | '.join(stderr_tail[-3:]) or 'no file'}")
        path = Path(result["path"])
        if not path.exists():
            raise Exception("Downloaded file not found")
        meeting.video_path = str(path)
        title = (result.get("title") or path.stem).strip()
        meeting.original_filename = f"{title}{path.suffix}"
        try:
            meeting.file_size = path.stat().st_size
        except OSError:
            pass
        await db.commit()
        await self._log(db, meeting.id, "INFO", f"Downloaded: {meeting.original_filename}")

    async def _transcribe(self, db, meeting, settings, out_dir) -> Path:
        cmd = T.build_command(
            meeting.video_path, out_dir,
            language=settings.get("transcriptionLanguage", "ru"),
            model=settings.get("whisperModel", "medium"),
            engine=settings.get("transcriptionEngine", "faster-whisper"),
            device=settings.get("whisperDevice", "auto"),
            initial_prompt=settings.get("transcriptionHint", ""),
            diarization=settings.get("diarizationBackend", "sherpa"),
            hf_token=settings.get("hfToken", ""))
        handoff = await self._gpu_acquire(db, meeting, settings)
        await self._log(db, meeting.id, "INFO", "Transcription started")
        try:
            output = await self._run_streaming(db, meeting, cmd)
        finally:
            if handoff:
                await self._gpu_release(db, meeting)
        path = Path(output) if output else out_dir / f"{Path(meeting.video_path).stem}_raw.txt"
        if not path.exists():
            raise Exception("Transcription produced no transcript file")
        return path

    async def _gpu_acquire(self, db, meeting, settings) -> bool:
        """Free the GPU (stop the local LLM) for GPU transcription, if enabled."""
        if not settings.get("gpuHandoff"):
            return False
        if settings.get("whisperDevice", "auto") not in ("cuda", "auto"):
            return False
        try:
            import gpu_handoff
            status = await asyncio.to_thread(
                gpu_handoff.acquire_status,
                int(settings.get("llamaPort", 8080) or 8080))
            if status == "freed":
                await self._log(
                    db, meeting.id, "INFO",
                    "GPU hand-off: stopped local LLM to free VRAM for transcription")
                return True
            if status == "idle":
                # Not a problem: nothing was running, so nothing needed unloading.
                await self._log(
                    db, meeting.id, "INFO",
                    "GPU hand-off: no local model was running, nothing to unload")
                return False
            await self._log(
                db, meeting.id, "WARNING",
                "GPU hand-off: the local model survived the stop; VRAM was NOT "
                "freed for transcription")
            return False
        except Exception as e:  # noqa: BLE001
            await self._log(db, meeting.id, "WARNING", f"GPU hand-off acquire failed: {e}")
            return False

    async def _gpu_release(self, db, meeting) -> None:
        try:
            import gpu_handoff
            restored = await asyncio.to_thread(gpu_handoff.release)
            if not restored:
                await self._log(
                    db, meeting.id, "WARNING",
                    "GPU hand-off: local LLM did not become ready after restart")
        except Exception as exc:  # noqa: BLE001
            await self._log(
                db, meeting.id, "WARNING",
                f"GPU hand-off release failed: {exc}")

    def _ai_kwargs(self, settings) -> dict:
        provider = settings.get("aiProvider", "local")
        endpoint = (settings.get("localEndpoint") or "").strip()
        if not endpoint and provider == "local":
            # An empty endpoint made ai_client fall back to LM Studio's port 1234,
            # so a run died with "Cannot connect to local API at
            # http://localhost:1234/v1" while the model was on the configured
            # llamaPort. The two settings must agree: derive one from the other.
            endpoint = f"http://127.0.0.1:{int(settings.get('llamaPort', 8080) or 8080)}/v1"
        return {
            "provider": provider,
            "api_key": settings.get("apiKey", ""),
            "endpoint": endpoint,
            "model": settings.get("aiModel", ""),
            "agent_command": settings.get("agentCommand", ""),
            "agent_cwd": settings.get("agentCwd", ""),
            "advanced": (settings.get("advancedSettings") or {}).get(provider),
            # long/reasoning-model controls (matter for 3-4h meetings on a local LLM)
            "timeout": int(settings.get("aiTimeout", 0) or 0),
            "no_think": bool(settings.get("disableReasoning", False)),
            # chunking threshold: 0 => ai_client default. Big-context models (Qwen 262k)
            # set this high so a whole 4h transcript goes in ONE pass (no context loss).
            "chunk_chars": int(settings.get("chunkChars", 0) or 0),
            # Chunking is opt-in: whole-transcript processing preserves the most
            # context and is the quality-first default. Users can enable chunking
            # explicitly for models with smaller context windows.
            "no_chunk": settings.get("chunkingEnabled", False) is False,
            # survive a local-model crash + watchdog restart (default on; cloud never
            # hits a connection error so it's a no-op there)
            "retries": int(settings.get("aiRetries", 3) or 0),
            "retry_delay": int(settings.get("aiRetryDelay", 60) or 60),
        }

    async def _next_version(self, db, meeting_id, kind) -> int:
        n = (await db.execute(select(func.count()).select_from(Artifact).where(
            Artifact.meeting_id == meeting_id, Artifact.kind == kind))).scalar_one()
        return int(n) + 1

    @staticmethod
    def _versioned_name(stem, kind, version, ext) -> str:
        # v1 = <stem>_<kind>.<ext>; v2+ = <stem>_<kind>_v<N>.<ext> (matches the desktop).
        suffix = "" if version <= 1 else f"_v{version}"
        return f"{stem}_{kind}{suffix}.{ext}"

    async def _contextual_memory_block(self, db, meeting, settings) -> str:
        """When 'useContextualMemory' is on, append the latest summaries of PRIOR
        meetings in the SAME project (same user) so the model keeps continuity.
        Strictly project-scoped + opt-in — a different-topic meeting is never mixed in.
        Bounded: last 3 meetings, ~1500 chars each, ~6000 total."""
        if not settings.get("useContextualMemory"):
            return ""
        # Fall back to the configured default project. Requiring a per-meeting tag
        # made the feature a no-op: uploads start processing immediately, so there
        # was no moment at which the tag could be applied first.
        project = (meeting.project or settings.get("projectId") or "").strip()
        if not project:
            return ""
        rows = (await db.execute(
            select(Meeting).where(
                Meeting.user_id == meeting.user_id, Meeting.project == project,
                Meeting.id != meeting.id, Meeting.summary_path.isnot(None))
            .order_by(Meeting.created_at.desc()).limit(3))).scalars().all()
        parts, total = [], 0
        for m in rows:
            try:
                text = Path(m.summary_path).read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            if not text:
                continue
            text = text[:1500]
            parts.append(f"### Встреча «{m.original_filename}» ({m.processed_at or ''}):\n{text}")
            total += len(text)
            if total >= 6000:
                break
        if not parts:
            return ""
        return ("\n\n---\nКонтекст из предыдущих встреч проекта «" + project +
                "» (для связности; не пересказывай их, только учитывай):\n\n"
                + "\n\n".join(parts))

    async def _summarize(self, db, meeting, settings, transcript, out_dir):
        prompt = (settings.get("prompt", "") or _DEFAULTS["prompt"])
        prompt += await self._contextual_memory_block(db, meeting, settings)
        cmd = S.build_command(
            prompt, transcript,
            output_language=S.resolve_output_language(settings),
            transcription_language=settings.get("transcriptionLanguage", "ru"),
            **self._ai_kwargs(settings))
        await self._log(db, meeting.id, "INFO", "Summary generation started")
        await self._set_progress(db, meeting, "status.summarizing", 85, "summary")
        text = await self._run_ai(cmd, meeting.id)
        version = await self._next_version(db, meeting.id, "summary")
        stem = Path(meeting.video_path).stem
        path = out_dir / self._versioned_name(stem, "summary", version, "txt")
        path.write_text(text, encoding="utf-8")
        db.add(Artifact(meeting_id=meeting.id, kind="summary", version=version,
                        path=str(path), provider=settings.get("aiProvider", "local")))
        await db.commit()
        return path, version

    async def _analyze(self, db, meeting, settings, transcript, summary, out_dir,
                       summary_version=0):
        features = A.enabled_features(settings)
        if not features:
            return None
        source, linked_summary_version = _analysis_source(
            settings, transcript, summary, summary_version)
        await self._log(db, meeting.id, "INFO", f"Analysis: {len(features)} features")
        results = A.empty_results()
        failed_features = []
        # The meeting's real date/time from its file name — the cabinet must not
        # produce a protocol dated differently from the desktop's for the same file.
        facts = A.protocol_facts(
            Path(meeting.video_path).name, duration=(meeting.duration or ""),
            language=A.resolve_output_language(settings))
        for i, feature in enumerate(features):
            pct = 90 + int(9 * i / max(1, len(features)))
            await self._set_progress(db, meeting, "status.analyzing", pct, f"analysis: {feature}")
            cmd = A.build_feature_command(
                feature, source, settings, facts=facts, **self._ai_kwargs(settings))
            # A model is sampled, not deterministic: the same prompt that returned
            # unusable JSON once returns clean JSON on the next pass. One second
            # try keeps a single flaky feature from failing the whole meeting (and
            # costing it its exports), without weakening the schema check.
            for attempt in (1, 2):
                try:
                    text = await self._run_ai(cmd, meeting.id)
                    parsed = A.parse_json_response(text)
                    if not A.is_valid_feature_result(feature, parsed):
                        raise ValueError(
                            "AI returned invalid JSON/schema; response starts with: "
                            + (text or "")[:300].replace("\n", " "))
                    if feature == "formalProtocol":
                        parsed = A.apply_protocol_facts(parsed, facts)
                    A.store_feature_result(results, feature, parsed)
                    break
                except Exception as e:
                    # Only an unparsable ANSWER (the ValueError above) is retried;
                    # a provider error or timeout has already spent ai_client's own
                    # retry budget and repeating it here just doubles the wait.
                    if attempt == 1 and isinstance(e, ValueError):
                        await self._log(db, meeting.id, "WARNING",
                                        f"Analysis '{feature}' failed, retrying: {e}")
                        continue
                    failed_features.append((feature, str(e)))
                    await self._log(db, meeting.id, "WARNING",
                                    f"Analysis '{feature}' failed: {e}")
        version = await self._next_version(db, meeting.id, "analysis")
        stem = Path(meeting.video_path).stem
        path = out_dir / self._versioned_name(stem, "analysis", version, "json")
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        db.add(Artifact(meeting_id=meeting.id, kind="analysis", version=version,
                        path=str(path), provider=settings.get("aiProvider", "local"),
                        source_summary_version=linked_summary_version))
        await db.commit()
        if failed_features:
            details = "; ".join(
                f"{feature}: {error}" for feature, error in failed_features[:3])
            raise RuntimeError(
                f"Analysis incomplete: {len(failed_features)}/{len(features)} "
                f"features failed. Partial artifact saved at {path}. {details}")
        return path

    # -- subprocess runners ------------------------------------------------
    async def _run_streaming(self, db, meeting, cmd) -> str:
        """Run a progress-streaming CLI (processor.py); return the output path."""
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=(cmd.process_environment()
                 if hasattr(cmd, "process_environment") else None))
        self._track(meeting.id, process)
        output_path, stderr_tail = None, []

        async def drain_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                msg = line.decode(errors="replace").strip()
                if msg:
                    stderr_tail.append(msg)
                    del stderr_tail[:-15]
        stderr_task = asyncio.create_task(drain_stderr())
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode(errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                if data.get("success") is True:
                    output_path = data.get("output")
                    continue
                if data.get("success") is False or "error" in data:
                    raise Exception(data.get("error", "Transcription failed"))
                await self._set_progress(
                    db, meeting, data.get("stage", ""), data.get("progress", 0),
                    data.get("details", ""))
            await process.wait()
            await stderr_task
        finally:
            self._untrack(meeting.id, process)
        if self.is_cancelled(meeting.id):
            raise self.Cancelled()
        if process.returncode != 0:
            raise Exception(f"Transcription exit {process.returncode}: {' | '.join(stderr_tail[-5:])}")
        return output_path

    async def _run_ai(self, cmd, meeting_id=0) -> str:
        """Run a single-shot AI CLI (ai_client.py); return stdout text."""
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=(cmd.process_environment()
                 if hasattr(cmd, "process_environment") else None))
        self._track(meeting_id, process)
        try:
            stdout, stderr = await process.communicate()
        finally:
            self._untrack(meeting_id, process)
        if self.is_cancelled(meeting_id):
            raise self.Cancelled()
        if process.returncode != 0:
            raise Exception(f"AI exit {process.returncode}: "
                            f"{stderr.decode(errors='replace').strip()[-400:]}")
        return stdout.decode(errors="replace").strip()

    async def _set_progress(self, db, meeting, stage: str, pct, details: str = ""):
        """Persist live progress/stage/ETA (throttled) AND broadcast over WebSocket,
        so the personal cabinet shows status across page reloads, not only live."""
        pct = int(max(0, min(100, pct or 0)))
        eta = None
        if meeting.processing_started_at and 0 < pct < 100:
            elapsed = (datetime.utcnow() - meeting.processing_started_at).total_seconds()
            if elapsed > 1:
                eta = int(elapsed * (100 - pct) / pct)   # simple rate-based estimate
        # Write to DB only on a stage change or a >=3% jump (avoid hammering SQLite).
        if stage != meeting.stage or pct - int(meeting.progress or 0) >= 3 or pct >= 100:
            meeting.stage = stage
            meeting.progress = pct
            meeting.eta_seconds = eta
            await db.commit()
        await manager.broadcast_progress(meeting.id, stage, pct, details)

    async def _maybe_export_gsheets(self, db, meeting, settings, summary_path, analysis_path):
        """Auto-append a row to the user's Google Sheet via their Apps Script webhook.
        Best-effort — never fails the job (mirrors the desktop pipeline)."""
        if not settings.get("googleSheetsIntegration"):
            return
        url = (settings.get("googleSheetsUrl") or "").strip()
        if not url:
            return
        try:
            from desktop.app.backend import gsheets
            summary_text = ""
            if summary_path and Path(summary_path).exists():
                summary_text = Path(summary_path).read_text(encoding="utf-8", errors="replace")
            analysis = {}
            if analysis_path and Path(analysis_path).exists():
                analysis = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
            transcript_text = ""
            if meeting.transcript_path and Path(meeting.transcript_path).exists():
                transcript_text = Path(meeting.transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            values = gsheets.build_values(
                meeting.original_filename, summary_text, analysis,
                duration=(meeting.duration or ""), transcript_text=transcript_text)
            gsheets.export(url, values,
                           token=(settings.get("googleSheetsToken") or "").strip())
            await self._log(db, meeting.id, "INFO", "Exported to Google Sheets")
        except Exception as e:  # noqa: BLE001
            await self._log(db, meeting.id, "WARNING", f"Google Sheets export failed: {e}")

    async def _log(self, db: AsyncSession, meeting_id: int, level: str, message: str):
        db.add(ProcessingLog(meeting_id=meeting_id, log_level=level, message=message))
        await db.commit()


# Global instance
worker = ProcessingWorker()
