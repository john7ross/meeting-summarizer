"""Live transcription & live summary via the backend ``live_stt.py`` /
``live_summary.py`` subprocesses.

Same shape as ``transcription.py`` and ``summarization.py``: this layer only
assembles argv and parses lines. No model is loaded here, no AI provider logic
is re-implemented here, and it stays Qt-free so it is testable without a window.

    python live_stt.py --engine <e> --model <m> --language <ru|en> \
        --channels <1|2> --transcript-file <path>          # PCM on stdin
    python live_summary.py --mode <update|regen|consolidate> --provider <p> \
        --state-file <path> [--chunk-file <path>] [--transcript-file <path>]

Both write JSON to stdout; secrets go through the environment (``SecureCommand``),
never argv, exactly like the batch passes.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import paths
from .command import SecureCommand

LIVE_STT_SCRIPT = paths.BACKEND_DIR / "live_stt.py"
LIVE_SUMMARY_SCRIPT = paths.BACKEND_DIR / "live_summary.py"

MODES = ("update", "regen", "consolidate")


def live_engines() -> tuple:
    """Engines that can run live, asked of the backend rather than hardcoded.

    The batch engine list went stale once already (sherpa-onnx/whisper.cpp/FunASR
    shipped while the copy in this layer did not know about them, and every
    meeting using one died at extraction). One source of truth, asked at runtime.
    """
    try:
        backend_dir = str(paths.BACKEND_DIR)
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from processing import live_engines as le
        return tuple(le.SUPPORTED)
    except Exception:                                  # noqa: BLE001
        return ("faster-whisper", "whisperx", "whisper", "vosk",
                "sherpa-onnx", "whisper-cpp")


def supports_live(engine: str) -> bool:
    return engine in live_engines()


@dataclass
class Segment:
    """One recognised utterance from the live worker."""
    index: int
    start: float
    duration: float
    timestamp: str
    source: str          # 'mic' | 'system' | 'mix'
    text: str
    forced: bool = False
    latency: float = 0.0
    queued: int = 0

    @property
    def line(self) -> str:
        """The transcript line, in the same shape the batch engines produce.

        Kept identical to what ``live_stt.py`` writes to disk: the panel and the
        file must not diverge, and this file can be fed straight into the normal
        summary/analysis pipeline, where the diarised shape is already parsed.
        """
        label = {"mic": "MIC", "system": "SYSTEM"}.get(self.source)
        return (f"{self.timestamp} [{label}]: {self.text}" if label
                else f"{self.timestamp} {self.text}")


@dataclass
class Status:
    """A non-transcript event: loading / ready / lag / warning / error / done."""
    kind: str
    message: str = ""
    detail: Optional[dict] = None

    def describe(self) -> str:
        """A message worth showing a user.

        ``ready`` carries its information in fields rather than in ``message``;
        naming the engine, the model and the device it actually got is how a user
        finds out that 'auto' resolved to the CPU, which is the difference
        between "live is slow" and "live is broken".
        """
        if self.kind == "ready" and self.detail:
            engine = self.detail.get("engine") or ""
            model = self.detail.get("model") or ""
            device = self.detail.get("device") or ""
            parts = [str(part) for part in (engine, model) if part]
            label = " / ".join(parts)
            return f"{label} ({device})" if device else label
        if self.kind == "lag" and self.detail:
            return str(self.detail.get("queued") or "")
        return self.message


def parse_line(line: str):
    """One stdout line -> ``Segment`` | ``Status`` | ``None``."""
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    kind = str(obj.get("type") or "")
    if kind == "segment":
        try:
            return Segment(
                index=int(obj.get("index") or 0),
                start=float(obj.get("start") or 0.0),
                duration=float(obj.get("duration") or 0.0),
                timestamp=str(obj.get("timestamp") or ""),
                source=str(obj.get("source") or "mix"),
                text=str(obj.get("text") or ""),
                forced=bool(obj.get("forced")),
                latency=float(obj.get("latency") or 0.0),
                queued=int(obj.get("queued") or 0))
        except (TypeError, ValueError):
            return None
    if not kind:
        return None
    return Status(kind=kind, message=str(obj.get("message") or ""), detail=obj)


def build_stt_command(*, engine="faster-whisper", model="", language="ru",
                      device="auto", initial_prompt="", channels=1,
                      sample_rate=16000, transcript_file="",
                      silence_ms=0, max_utterance_ms=0,
                      python_exe=None, script=None) -> SecureCommand:
    """argv for the streaming transcription worker. Raises on an unusable engine."""
    if not supports_live(engine):
        raise ValueError(
            f"Engine {engine!r} cannot run live; expected one of {live_engines()}.")
    python_exe = Path(python_exe) if python_exe else paths.python_executable()
    script = Path(script) if script else LIVE_STT_SCRIPT
    command = SecureCommand([
        str(python_exe), str(script),
        "--engine", str(engine),
        "--language", str(language or "ru"),
        "--device", str(device or "auto"),
        "--channels", str(int(channels) if channels in (1, 2) else 1),
        "--sample-rate", str(int(sample_rate or 16000)),
    ])
    if model:
        command += ["--model", str(model)]
    if initial_prompt:
        command += ["--initial-prompt", str(initial_prompt)]
    if transcript_file:
        command += ["--transcript-file", str(transcript_file)]
    if silence_ms and int(silence_ms) > 0:
        command += ["--silence-ms", str(int(silence_ms))]
    if max_utterance_ms and int(max_utterance_ms) > 0:
        command += ["--max-utterance-ms", str(int(max_utterance_ms))]
    return command


def build_summary_command(mode: str, state_file, *, chunk_file="",
                          recent_file="", transcript_file="", language="ru",
                          provider="local", api_key="", endpoint="", model="",
                          advanced=None, agent_command="", agent_cwd="",
                          timeout=0, no_think=False, retries=0, retry_delay=0,
                          max_transcript_chars=0,
                          python_exe=None, script=None) -> SecureCommand:
    """argv for one live-summary pass. Raises on an unknown mode.

    The provider arguments are the SAME ones the post-meeting summary uses, so a
    user who configured a local endpoint, a cloud key, an agent CLI or the
    built-in model gets live summaries through it with no extra setup.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown live summary mode {mode!r}; expected {MODES}.")
    python_exe = Path(python_exe) if python_exe else paths.python_executable()
    script = Path(script) if script else LIVE_SUMMARY_SCRIPT
    command = SecureCommand([
        str(python_exe), str(script),
        "--mode", str(mode),
        "--provider", str(provider or "local"),
        "--endpoint", str(endpoint or ""),
        "--language", str(language or "ru"),
        "--state-file", str(state_file),
    ], environment={"MEETING_SUMMARIZER_API_KEY": api_key or ""})
    if chunk_file:
        command += ["--chunk-file", str(chunk_file)]
    if recent_file:
        command += ["--recent-file", str(recent_file)]
    if transcript_file:
        command += ["--transcript-file", str(transcript_file)]
    if model:
        command += ["--model", str(model)]
    if advanced:
        command.environment["MEETING_SUMMARIZER_ADVANCED"] = json.dumps(
            advanced, ensure_ascii=False)
    if agent_command:
        command += ["--agent-command", str(agent_command)]
    if agent_cwd:
        command += ["--agent-cwd", str(agent_cwd)]
    if timeout and int(timeout) > 0:
        command += ["--timeout", str(int(timeout))]
    if no_think:
        command += ["--no-think"]
    if retries and int(retries) > 0:
        command += ["--retries", str(int(retries))]
        if retry_delay and int(retry_delay) > 0:
            command += ["--retry-delay", str(int(retry_delay))]
    if max_transcript_chars and int(max_transcript_chars) > 0:
        command += ["--max-transcript-chars", str(int(max_transcript_chars))]
    return command


def parse_summary_result(stdout: str) -> Optional[dict]:
    """The final JSON object printed by ``live_summary.py``, or ``None``.

    The last non-empty line is taken: a chatty provider can print progress notes
    to stdout before the result, and losing a good summary to that would be
    exactly the failure mode this feature is supposed to avoid.
    """
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and ("success" in obj or "updated_state" in obj):
            return obj
    return None


def render_summary(state: dict, delta: str = "") -> str:
    """Turn the state object into the text shown in the Live Summary panel.

    Everything the model extracted is displayed — a decision or an action item
    that exists only inside a JSON file nobody opens has not been delivered.
    """
    if not isinstance(state, dict):
        return ""
    blocks = []
    summary = (state.get("short_summary") or "").strip()
    if summary:
        blocks.append(summary)

    def bullets(title, items, render):
        rows = [render(item) for item in (items or [])]
        rows = [row for row in rows if row]
        if rows:
            blocks.append(f"## {title}\n" + "\n".join(f"- {row}" for row in rows))

    bullets("Решения", state.get("decisions"), lambda d: str(d).strip())
    bullets("Задачи", state.get("action_items"), _render_action_item)
    bullets("Открытые вопросы", state.get("open_questions"),
            lambda q: str(q).strip())
    if delta and delta.strip():
        blocks.append(f"## Новое\n- {delta.strip()}")
    return "\n\n".join(blocks).strip()


def _render_action_item(item) -> str:
    if not isinstance(item, dict):
        return str(item).strip()
    task = str(item.get("task") or "").strip()
    if not task:
        return ""
    owner = str(item.get("owner") or "").strip()
    deadline = str(item.get("deadline") or "").strip()
    parts = [f"{owner}: {task}" if owner else task]
    if deadline:
        parts.append(f"(срок: {deadline})")
    return " ".join(parts)
