"""Media helpers for trimming a recording before transcription.

Back-to-back meetings often land in ONE file. Transcribing it whole produces a
summary/analysis that blends several meetings, so the user picks a time range
per meeting and each range is processed as its own job.

Rather than teach every engine about offsets, we cut the chosen range into its
own file with ffmpeg and feed THAT to the normal pipeline — so naming, folders,
versions and exports all work unchanged, and each segment gets a separate
transcript/summary/analysis.

Audio-only output (16 kHz mono WAV) is what every ASR engine wants anyway: the
cut is accurate (decode-based seek), small, and fast. Qt-free so it can be
unit-tested.
"""
from __future__ import annotations

import subprocess
import re
from pathlib import Path

from .. import paths

_CREATE_NO_WINDOW = 0x08000000


def _hidden_startupinfo():
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _run(cmd: list, timeout: float):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace",
                          creationflags=_CREATE_NO_WINDOW,
                          startupinfo=_hidden_startupinfo())


def format_timecode(seconds: float) -> str:
    """Seconds -> 'H:MM:SS' (always with hours, so fields line up)."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def duration_from_transcript(text: str) -> str:
    """Meeting length taken from the transcript's last ``[HH:MM:SS]`` marker.

    The single fallback used everywhere a meeting's duration is reported when
    the history/DB row has none recorded (older entries, URL downloads, some
    engines). Keeping one implementation is what stops the analysis panel from
    showing a real length while an export writes "N/A" for the same meeting.
    """
    marks = re.findall(r"\[(\d{1,2}):(\d{2}):(\d{2})\]", text or "")
    if not marks:
        return ""
    h, m, s = (int(x) for x in marks[-1])
    total = h * 3600 + m * 60 + s
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh}h {mm}m {ss}s" if hh else f"{mm}m {ss}s"


def parse_timecode(text: str) -> float:
    """'1:05:30' / '05:30' / '90' -> seconds. Raises ValueError if unparsable."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty timecode")
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", text):
        raise ValueError(f"bad timecode: {text!r}")
    parts = [float(p) for p in text.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def probe_duration(path) -> float:
    """Media duration in seconds via ffprobe; 0.0 if it cannot be determined."""
    try:
        out = _run([paths.ffprobe_executable(), "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(path)], timeout=60)
        return float((out.stdout or "").strip())
    except Exception:      # noqa: BLE001 — unknown duration is not fatal
        return 0.0


def segment_filename(src, start: float, end: float) -> str:
    """'<stem> (0-00-00 - 1-05-30).wav' — self-describing, filesystem-safe, and
    it becomes the artifact folder name downstream."""
    stem = Path(src).stem
    tag = f"{format_timecode(start)} - {format_timecode(end)}".replace(":", "-")
    return f"{stem} ({tag}).wav"


def cut_segment(src, dst, start: float, end: float, timeout: float = 3600) -> str:
    """Extract [start, end) of *src* into *dst* as 16 kHz mono WAV.

    ``-ss``/``-to`` are placed AFTER ``-i`` so the seek is decode-accurate (a
    keyframe-snapped cut would drift by seconds and clip speech). Raises
    RuntimeError with ffmpeg's message if the cut fails."""
    if end <= start:
        raise ValueError("end must be greater than start")
    dst = str(dst)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    cmd = [paths.ffmpeg_executable(), "-y", "-i", str(src),
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    try:
        proc = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out while cutting the segment") from exc
    if proc.returncode != 0 or not Path(dst).exists():
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg failed: " + " | ".join(tail))
    return dst
