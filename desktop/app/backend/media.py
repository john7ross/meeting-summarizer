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


_NAME_DATE_PATTERNS = (
    # 2026-08-17 / 2026_08_17 / 2026.08.17   (year first)
    (r"(?P<y>20\d{2})[-_.](?P<m>\d{2})[-_.](?P<d>\d{2})", ("y", "m", "d")),
    # 17.08.2026 / 17-08-2026               (day first; two digits required, so a
    # version string like "v1.2.2026" is not mistaken for a date)
    (r"(?P<d>\d{2})[-_.](?P<m>\d{2})[-_.](?P<y>20\d{2})", ("y", "m", "d")),
    # 20260817
    (r"(?P<y>20\d{2})(?P<m>\d{2})(?P<d>\d{2})", ("y", "m", "d")),
)
# Time following the date: 15-33-43 / 15.33.43 / 15:33 / 153343
_NAME_TIME = re.compile(
    r"^[ _\-.tT]*(?:(?P<h>[01]\d|2[0-3])[-_.:](?P<mi>[0-5]\d)(?:[-_.:](?P<s>[0-5]\d))?"
    r"|(?P<h2>[01]\d|2[0-3])(?P<mi2>[0-5]\d)(?P<s2>[0-5]\d))")
# A real file extension: a short alphanumeric tail (.mkv/.mp4/.m4a/.webm), never
# the '.2026 15-33' that a dotted date leaves at the end of a stem.
_FILE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def meeting_datetime_from_name(name) -> tuple[str, str]:
    """('2026-08-17', '15:33') from a recorder's file name; '' for what is absent.

    The owner's recordings are named ``2026-08-17 15-33-43.mkv`` — the meeting's
    real date and start time are right there, and letting the model guess them
    produced "24.10.2023" in 13 of 14 real analyses. One implementation, shared by
    the formal protocol, the Obsidian note and the Google Sheets row.

    Returns ISO date and ``HH:MM`` start, each empty when the name does not carry
    it. Values are validated (month 1-12, day 1-31, hour 0-23), so arbitrary digit
    groups are not read as a timestamp.
    """
    # Callers pass either a file name or an already-stripped stem. Cutting at the
    # last dot unconditionally ate the date out of a stem that carries one:
    # 'Планёрка 17.08.2026 15-33' has no extension, but Path treats '.2026 15-33'
    # as one and leaves 'Планёрка 17.08' — the note then got today's date instead
    # of the meeting's. Strip only a tail that really looks like an extension.
    raw = str(name or "")
    stem = Path(raw).stem if _FILE_EXTENSION.search(raw) else raw
    for pattern, _ in _NAME_DATE_PATTERNS:
        for match in re.finditer(pattern, stem):
            year, month, day = (int(match.group("y")), int(match.group("m")),
                                int(match.group("d")))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            date_iso = f"{year:04d}-{month:02d}-{day:02d}"
            tail = stem[match.end():]
            time_match = _NAME_TIME.match(tail)
            if not time_match:
                return date_iso, ""
            hour = time_match.group("h") or time_match.group("h2")
            minute = time_match.group("mi") or time_match.group("mi2")
            return date_iso, f"{int(hour):02d}:{minute}"
    return "", ""


def parse_duration_label(text) -> int:
    """'12м 47с' / '12m 47s' / '1h 5m 3s' -> seconds (0 when unparsable).

    Durations are stored as the display string the app produced, in either
    language, so both spellings have to be understood.
    """
    seconds = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*([hчmмsс])", str(text or ""), re.IGNORECASE):
        unit = unit.lower()
        found = True
        if unit in ("h", "ч"):
            seconds += int(value) * 3600
        elif unit in ("m", "м"):
            seconds += int(value) * 60
        else:
            seconds += int(value)
    return seconds if found else 0


def shift_clock(start_hhmm: str, seconds: int) -> str:
    """'15:33' + 767s -> '15:45' (wraps past midnight; '' if start is unusable)."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (start_hhmm or "").strip())
    if not match or seconds <= 0:
        return ""
    total = (int(match.group(1)) * 60 + int(match.group(2))
             + int(round(seconds / 60.0))) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


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
