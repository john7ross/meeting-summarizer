"""FFmpeg audio extraction and chunking for the transcription engines.

Resolves the BUNDLED ffmpeg/ffprobe first and only then falls back to PATH,
so a distributed copy never depends on what happens to be installed on the
recipient's machine.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from .progress import log_progress


def _backend_dir():
    return Path(__file__).resolve().parents[1]


def _resolve_ffmpeg_path():
    backend_dir = _backend_dir()
    candidates = [
        backend_dir / 'FFmpeg' / 'ffmpeg.exe',
        backend_dir / '..' / 'resources' / 'FFmpeg' / 'ffmpeg.exe'
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return 'ffmpeg'


def _resolve_ffprobe_path(ffmpeg_path):
    if ffmpeg_path.endswith('ffmpeg.exe'):
        return ffmpeg_path.replace('ffmpeg.exe', 'ffprobe.exe')
    return 'ffprobe'


def extract_audio(video_path, output_audio_path, tracer):
    """Extract mono 16kHz WAV audio from a video file."""
    tracer.start_span("extract_audio", {"video_path": video_path})

    start_time = time.time()

    log_progress("status.extracting", 10, "Extracting audio from video...")
    sys.stdout.flush()

    try:
        video_path = os.path.normpath(video_path)
        output_audio_path = os.path.normpath(output_audio_path)

        if not os.path.exists(video_path):
            raise Exception(f"Video file not found: {video_path}")

        ffmpeg_path = _resolve_ffmpeg_path()
        cmd = [
            ffmpeg_path,
            '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            '-y',
            output_audio_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise Exception(f"FFmpeg error: {result.stderr}")

        elapsed = time.time() - start_time
        print(f"DEBUG: About to send audio extraction message: {elapsed:.1f}s", file=sys.stderr, flush=True)
        log_progress("status.extracting", 25, f"Audio extracted in {elapsed:.1f}s")
        print("DEBUG: Audio extraction message sent", file=sys.stderr, flush=True)
        sys.stdout.flush()
        sys.stderr.flush()

        tracer.end_span()
        return True

    except Exception as e:
        tracer.end_span()
        log_progress("status.error", 0, f"Failed to extract audio: {str(e)}")
        raise


def peak_dbfs(audio_path):
    """Peak level of an audio file in dBFS, or ``None`` if it cannot be measured.

    Used to tell "nobody spoke" apart from "nothing was recorded". A track of
    digital silence reports about -91 dB, the noise floor of 16-bit PCM.
    """
    try:
        ffmpeg_path = _resolve_ffmpeg_path()
        null_sink = "NUL" if os.name == "nt" else "/dev/null"
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-i", audio_path,
             "-af", "volumedetect", "-f", "null", null_sink],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        for line in (result.stderr or "").splitlines():
            if "max_volume:" in line:
                return float(line.split("max_volume:")[1].strip().split()[0])
    except Exception:      # noqa: BLE001 - a missing measurement must never fail a run
        return None
    return None


def split_audio_chunks(audio_path, chunk_duration=600):
    """
    Split audio into chunks.

    Returns a list of chunk file paths. If splitting fails, returns the full audio file path.
    """
    log_progress("status.extracting", 35, "Splitting audio into chunks...")

    chunks = []
    base_name = Path(audio_path).stem
    output_dir = Path(audio_path).parent

    try:
        ffmpeg_path = _resolve_ffmpeg_path()

        probe_cmd = [
            _resolve_ffprobe_path(ffmpeg_path),
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ]

        duration_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_duration = float(duration_result.stdout.strip())

        chunk_index = 0
        start_time = 0

        while start_time < total_duration:
            chunk_path = output_dir / f"{base_name}_chunk_{chunk_index}.wav"

            cmd = [
                ffmpeg_path,
                '-i', audio_path,
                '-ss', str(start_time),
                '-t', str(chunk_duration),
                '-acodec', 'copy',
                '-y',
                str(chunk_path)
            ]

            subprocess.run(cmd, capture_output=True)
            chunks.append(str(chunk_path))

            start_time += chunk_duration
            chunk_index += 1

        log_progress("status.transcribing", 40, f"Split into {len(chunks)} chunks, starting transcription...")
        return chunks

    except Exception as e:
        log_progress("status.error", 0, f"Failed to split audio: {str(e)}")
        return [audio_path]
