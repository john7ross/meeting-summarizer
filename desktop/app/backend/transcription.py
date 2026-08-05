"""Transcription via the existing backend ``processor.py`` subprocess.

The native client does not re-implement transcription; it drives the same CLI
the Electron app uses, so the verified backend stays untouched::

    python processor.py --video <v> --language <ru|en> --model <size>
        --engine <whisper|faster-whisper|whisperx|vosk>
        --device <auto|cuda|cpu> --output-dir <dir>

stdout carries newline-delimited JSON: progress objects
``{"stage", "progress", "details"}`` followed by a terminal result object
``{"success", "output", "trace"}`` or ``{"success": false, "error": ...}``.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Union

from .. import paths
from .command import SecureCommand

def _shipped_engines() -> tuple:
    """Engines this build can actually run, taken from the registry.

    This list used to be hardcoded and went stale: sherpa-onnx, whisper.cpp and
    FunASR shipped, appeared in both front-ends' settings, and then every meeting
    using one died at extraction with "Unknown engine". The registry is the only
    place that knows which engines exist and which have an adapter, so ask it.
    """
    try:
        import sys
        from .. import paths
        backend_dir = str(paths.ROOT / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        import engines_registry as reg
        # ``extra`` marks a download-only model pack, not a selectable engine.
        engines = tuple(e for e, spec in reg.ENGINES.items()
                        if reg.is_implemented(e) and not spec.get("extra"))
        if engines:
            return engines
    except Exception:  # noqa: BLE001 - never let a registry hiccup block transcription
        pass
    return ("whisper", "faster-whisper", "whisperx", "vosk")


ENGINES = _shipped_engines()


@dataclass
class ProgressEvent:
    stage: str
    progress: int
    details: str = ""


@dataclass
class ResultEvent:
    success: bool
    output: Optional[str] = None
    trace: Optional[str] = None
    error: Optional[str] = None


Event = Union[ProgressEvent, ResultEvent]


def build_command(video, output_dir, *, language="ru", model="medium",
                  engine="faster-whisper", device="auto", initial_prompt="",
                  diarization="sherpa", hf_token="",
                  python_exe=None, processor_script=None) -> list[str]:
    """Build the argv for one transcription run. Raises on an unknown engine.

    ``initial_prompt`` is an optional transcription hint (vocabulary/terms) passed
    only to whisper-family engines; empty → no hint (the neutral default).
    """
    if engine not in ENGINES:
        raise ValueError(f"Unknown engine {engine!r}; expected one of {ENGINES}.")
    python_exe = Path(python_exe) if python_exe else paths.python_executable()
    processor_script = (Path(processor_script) if processor_script
                        else paths.PROCESSOR_SCRIPT)
    command = SecureCommand([
        str(python_exe), str(processor_script),
        "--video", str(video),
        "--language", language,
        "--model", model,
        "--engine", engine,
        "--device", device,
        "--output-dir", str(output_dir),
    ], environment={"MEETING_SUMMARIZER_HF_TOKEN": hf_token or ""})
    if initial_prompt:
        command += ["--initial-prompt", str(initial_prompt)]
    if engine == "whisperx":
        command += ["--diarization", str(diarization or "sherpa")]
    return command


def parse_event(line: str) -> Optional[Event]:
    """Parse one stdout line into a ProgressEvent/ResultEvent, or None."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "success" in obj:
        return ResultEvent(
            success=bool(obj.get("success")),
            output=obj.get("output"),
            trace=obj.get("trace"),
            error=obj.get("error"),
        )
    if "stage" in obj or "progress" in obj:
        try:
            progress = int(obj.get("progress", 0) or 0)
        except (TypeError, ValueError):
            progress = 0
        return ProgressEvent(
            stage=str(obj.get("stage", "")),
            progress=progress,
            details=str(obj.get("details", "")),
        )
    return None


def iter_events(command, *, cwd=None) -> Iterator[Event]:
    """Run the transcription subprocess, yielding events as they arrive.

    Qt-free so it can be unit-tested and reused by any worker layer. If the
    process exits non-zero without emitting a result, a synthetic failing
    ResultEvent is yielded carrying the captured stderr.
    """
    proc = subprocess.Popen(
        list(command), cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace", bufsize=1,
        env=(command.process_environment()
             if hasattr(command, "process_environment") else None),
    )
    saw_result = False
    assert proc.stdout is not None
    for line in proc.stdout:
        event = parse_event(line)
        if event is None:
            continue
        if isinstance(event, ResultEvent):
            saw_result = True
        yield event
    proc.wait()
    stderr = proc.stderr.read() if proc.stderr else ""
    if not saw_result and proc.returncode != 0:
        yield ResultEvent(success=False,
                          error=(stderr.strip() or
                                 f"processor.py exited with code {proc.returncode}"))
