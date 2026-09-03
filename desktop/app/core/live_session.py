"""Live session — streaming transcription and a rolling summary while recording.

One object owns everything that happens *during* a meeting:

* a long-lived ``live_stt.py`` process fed with the same PCM the WAV receives;
* the chunking/scheduling rules that decide when the summary is worth updating;
* one ``live_summary.py`` process per update.

Two rules shape the whole design.

**Recording never pays for live.** The tap is one-way: PCM is written to the WAV
first and only then offered to the worker. If the worker is slow, dies, or was
never started, the recording is untouched — and the meeting still goes through
the normal pipeline afterwards, transcribed properly by the configured engine.
Live is a draft; the file is the record.

**A summary that is on screen never disappears.** Only a valid answer replaces
it. A failed update, an unparsable answer, a killed process — all of them leave
the last good summary exactly where it is and put the complaint in a separate
status line. This is the failure that makes a live summary feel broken, and it
is worth more code than it looks.

Update strategy is chosen from the provider, because the trade-off is entirely
about what tokens cost:

* a LOCAL model (own endpoint, built-in model, agent CLI) rebuilds the summary
  from the transcript every time — tokens are free, so there is no reason to
  accept drift;
* a metered CLOUD provider gets the hybrid: cheap incremental updates, periodic
  consolidation, and a full rebuild from the transcript often enough that a
  mistake cannot set. Mic Recorder's design notes chose the rebuild and shipped
  the increment; this picks per provider instead of picking once for everyone.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .. import paths
from ..backend import live as live_backend
from .worker import utf8_process_environment

# Providers whose tokens cost nothing but local time.
LOCAL_PROVIDERS = ("local", "gemma", "agent")

DEFAULT_INTERVAL_SECONDS = 30
MIN_CHUNK_CHARS = 80          # do not wake a model for two words
MAX_CHUNK_CHARS = 4000        # cap the chunk when a model answers slowly
RECENT_BUFFER_SECONDS = 120   # raw context that helps with cut-off thoughts
CONSOLIDATE_EVERY = 5         # cloud only: tidy the state every N updates
REGEN_EVERY = 8               # cloud only: rebuild from the transcript every N


class LiveSession(QObject):
    """Drives live transcription + live summary for one recording."""

    # One recognised utterance (backend.live.Segment).
    segment = Signal(object)
    # Transcription state: kind ('loading'|'ready'|'lag'|'warning'|'error'|'done'), message.
    status = Signal(str, str)
    # Rendered summary text, and a separate status line ('' when all is well).
    # The text argument is ALWAYS the last good summary, never a placeholder.
    summary = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._buffer = ""
        self._transcript_path = ""
        self._state_path = ""
        self._chunk_path = ""
        self._recent_path = ""
        self._summary_command = None
        self._summary_proc: Optional[QProcess] = None
        self._summary_text = ""
        self._summary_status = ""
        self._chunk_lines: list = []
        self._recent: list = []           # (timestamp, line)
        self._last_update = 0.0
        self._updates = 0
        self._max_updates = 0
        self._interval = DEFAULT_INTERVAL_SECONDS
        self._strategy = "auto"
        self._provider = "local"
        self._request_seq = 0
        self._running = False
        self._summary_enabled = False
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._maybe_update_summary)

    # -- state -----------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def summary_text(self) -> str:
        """The last good summary. Survives ``stop()`` until the next ``start()``,
        so a user can still read it after the meeting ended."""
        return self._summary_text

    @property
    def transcript_path(self) -> str:
        return self._transcript_path

    @property
    def summary_path(self) -> str:
        return self._state_path

    @property
    def updates(self) -> int:
        """How many summary passes were spent — shown so a cloud bill is visible."""
        return self._updates

    # -- lifecycle -------------------------------------------------------
    def start(self, settings: dict, artifacts_stem, *, channels: int = 1,
              sample_rate: int = 16000) -> bool:
        """Start the streaming worker. False (with a ``status``) if it cannot run."""
        if self._running:
            return True
        stem = Path(artifacts_stem)
        stem.parent.mkdir(parents=True, exist_ok=True)
        self._transcript_path = str(stem.with_name(stem.name + "_live_transcript.txt"))
        self._state_path = str(stem.with_name(stem.name + "_live_summary.json"))
        self._chunk_path = str(stem.with_name(stem.name + "_live_chunk.txt"))
        self._recent_path = str(stem.with_name(stem.name + "_live_recent.txt"))

        engine = (settings.get("liveEngine") or "").strip() \
            or (settings.get("transcriptionEngine") or "faster-whisper")
        model = (settings.get("liveModel") or "").strip() \
            or (settings.get("whisperModel") or "")
        try:
            command = live_backend.build_stt_command(
                engine=engine, model=model,
                language=(settings.get("transcriptionLanguage") or "ru"),
                device=(settings.get("whisperDevice") or "auto"),
                initial_prompt=(settings.get("transcriptionHint") or ""),
                channels=channels, sample_rate=sample_rate,
                transcript_file=self._transcript_path)
        except ValueError as exc:
            self.status.emit("error", str(exc))
            return False

        proc = QProcess(self)
        proc.setProcessEnvironment(utf8_process_environment())
        proc.setProgram(command[0])
        proc.setArguments([str(part) for part in command[1:]])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        self._buffer = ""
        proc.start()

        self._reset_summary_state()
        self._configure_summary(settings)
        self._running = True
        self._timer.start()
        return True

    def feed(self, pcm: bytes, channels: int = 1, rate: int = 16000) -> None:
        """Hand recorded PCM to the worker. Silently ignored when not running —
        the recorder must never have to know whether live is on."""
        if not self._running or self._proc is None:
            return
        if self._proc.state() == QProcess.NotRunning:
            return
        try:
            self._proc.write(pcm)
        except (RuntimeError, OSError):
            # The worker died; the recording continues regardless.
            pass

    def stop(self) -> None:
        """End live processing. The summary text stays available for reading."""
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        proc, self._proc = self._proc, None
        if proc is not None and proc.state() != QProcess.NotRunning:
            proc.closeWriteChannel()
            # Give the worker a moment to drain the queue it is still decoding;
            # whatever it emits in that window is a real part of the meeting.
            if not proc.waitForFinished(15000):
                proc.kill()
                proc.waitForFinished(3000)
            self._drain_stdout()
        summary_proc, self._summary_proc = self._summary_proc, None
        if summary_proc is not None and summary_proc.state() != QProcess.NotRunning:
            summary_proc.kill()
        self._cleanup_scratch()

    # -- transcription stream --------------------------------------------
    def _on_stdout(self) -> None:
        if self._proc is None:
            return
        self._buffer += bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", "replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._consume(line)

    def _drain_stdout(self) -> None:
        for line in self._buffer.splitlines():
            self._consume(line)
        self._buffer = ""

    def _consume(self, line: str) -> None:
        event = live_backend.parse_line(line)
        if event is None:
            return
        if isinstance(event, live_backend.Segment):
            self.segment.emit(event)
            self._append_transcript(event)
            return
        self.status.emit(event.kind, event.describe())

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.status.emit("error", "live transcription worker failed to start")

    def _on_finished(self, exit_code: int, _status=None) -> None:
        self._drain_stdout()
        if exit_code != 0 and self._running:
            stderr = ""
            if self._proc is not None:
                stderr = bytes(self._proc.readAllStandardError()).decode(
                    "utf-8", "replace").strip()
            self.status.emit("error", stderr or f"live worker exited ({exit_code})")

    # -- summary scheduling ----------------------------------------------
    def _configure_summary(self, settings: dict) -> None:
        self._summary_enabled = bool(settings.get("liveSummary"))
        if not self._summary_enabled:
            return
        self._provider = (settings.get("aiProvider") or "local").strip()
        self._strategy = (settings.get("liveSummaryStrategy") or "auto").strip()
        try:
            self._interval = max(10, int(settings.get("liveSummaryInterval")
                                         or DEFAULT_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            self._interval = DEFAULT_INTERVAL_SECONDS
        try:
            self._max_updates = max(0, int(settings.get("liveSummaryMaxUpdates") or 0))
        except (TypeError, ValueError):
            self._max_updates = 0
        self._summary_settings = dict(settings)
        self._last_update = time.time()

    def _reset_summary_state(self) -> None:
        self._summary_text = ""
        self._summary_status = ""
        self._chunk_lines = []
        self._recent = []
        self._updates = 0
        self._request_seq = 0
        self._last_update = time.time()
        for path in (self._state_path, self._chunk_path, self._recent_path):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self.summary.emit("", "")

    def _append_transcript(self, segment) -> None:
        if not self._summary_enabled:
            return
        now = time.time()
        self._chunk_lines.append(segment.line)
        self._recent.append((now, segment.line))
        cutoff = now - RECENT_BUFFER_SECONDS
        self._recent = [row for row in self._recent if row[0] >= cutoff]
        self._maybe_update_summary()

    def _chunk_text(self) -> str:
        return "\n".join(self._chunk_lines)

    def _should_update(self) -> bool:
        if not (self._running and self._summary_enabled):
            return False
        if self._summary_proc is not None:
            return False
        if self._max_updates and self._updates >= self._max_updates:
            return False
        chars = len(self._chunk_text())
        if chars < MIN_CHUNK_CHARS:
            return False
        if chars >= MAX_CHUNK_CHARS:
            return True
        return (time.time() - self._last_update) >= self._interval

    def _next_mode(self) -> str:
        """Which pass to run, given provider and the user's explicit choice."""
        if self._strategy == "regen":
            return "regen"
        if self._strategy == "auto" and self._provider in LOCAL_PROVIDERS:
            return "regen"
        step = self._updates + 1
        if step % REGEN_EVERY == 0:
            return "regen"
        if step % CONSOLIDATE_EVERY == 0:
            return "consolidate"
        return "update"

    def _maybe_update_summary(self) -> None:
        if not self._should_update():
            if (self._max_updates and self._updates >= self._max_updates
                    and self._summary_status != "limit"):
                self._summary_status = "limit"
                self.summary.emit(self._summary_text, "limit")
            return
        mode = self._next_mode()
        chunk = self._chunk_text()
        self._chunk_lines = []

        settings = getattr(self, "_summary_settings", {})
        try:
            Path(self._chunk_path).write_text(chunk, encoding="utf-8")
            Path(self._recent_path).write_text(
                "\n".join(line for _ts, line in self._recent), encoding="utf-8")
        except OSError as exc:
            self.summary.emit(self._summary_text, f"error:{exc}")
            return

        try:
            command = live_backend.build_summary_command(
                mode, self._state_path,
                chunk_file=self._chunk_path,
                recent_file=self._recent_path,
                transcript_file=self._transcript_path,
                language=(settings.get("outputLanguage")
                          if settings.get("outputLanguage") in ("ru", "en")
                          else settings.get("transcriptionLanguage") or "ru"),
                provider=self._provider,
                api_key=(settings.get("apiKey") or ""),
                endpoint=(settings.get("localEndpoint") or ""),
                model=(settings.get("aiModel") or ""),
                advanced=(settings.get("advancedSettings") or {}).get(self._provider),
                agent_command=(settings.get("agentCommand") or ""),
                agent_cwd=(settings.get("agentCwd") or ""),
                no_think=bool(settings.get("disableReasoning")))
        except ValueError as exc:
            self.summary.emit(self._summary_text, f"error:{exc}")
            return

        self._request_seq += 1
        seq = self._request_seq
        proc = QProcess(self)
        env = utf8_process_environment()
        for key, value in getattr(command, "environment", {}).items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.setProgram(command[0])
        proc.setArguments([str(part) for part in command[1:]])
        proc.finished.connect(lambda code, _s=None, s=seq, p=proc:
                              self._on_summary_done(s, p, code))
        proc.errorOccurred.connect(
            lambda _e, s=seq, p=proc: self._on_summary_done(s, p, -1))
        self._summary_proc = proc
        self.summary.emit(self._summary_text, f"updating:{mode}")
        proc.start()

    def _on_summary_done(self, seq: int, proc: QProcess, exit_code: int) -> None:
        if proc is not self._summary_proc:
            return                     # a stale pass; its answer is out of date
        self._summary_proc = None
        if seq != self._request_seq:
            return
        stdout = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
        result = live_backend.parse_summary_result(stdout)
        self._last_update = time.time()
        if not result or not result.get("success"):
            if result and result.get("skipped"):
                # Nothing to summarise yet — not a failure, nothing to say.
                self.summary.emit(self._summary_text, "")
                return
            reason = ""
            if result:
                reason = str(result.get("error") or "")
            if not reason:
                reason = bytes(proc.readAllStandardError()).decode(
                    "utf-8", "replace").strip() or f"exit code {exit_code}"
            # The text argument is the LAST GOOD summary, untouched.
            self.summary.emit(self._summary_text, f"error:{reason}")
            return
        self._updates = int(result.get("updates") or (self._updates + 1))
        rendered = live_backend.render_summary(
            result.get("updated_state") or {}, str(result.get("live_delta") or ""))
        if rendered:
            self._summary_text = rendered
        self._summary_status = ""
        self.summary.emit(self._summary_text, "")

    def _cleanup_scratch(self) -> None:
        """Remove the per-update scratch files; transcript and state stay."""
        for path in (self._chunk_path, self._recent_path):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def live_artifact_stem(recording_path) -> Path:
    """Where the live artifacts for a recording live: next to it, same stem.

    Keeping them beside the WAV means they are found by whoever finds the
    recording, survive the app being closed, and need no new index or database
    entry to be useful.
    """
    path = Path(recording_path)
    return path.with_suffix("")


def default_live_stem(root=None) -> Path:
    base = Path(root) if root else paths.ROOT
    return base / "recordings" / time.strftime("live %Y-%m-%d %H-%M-%S")
