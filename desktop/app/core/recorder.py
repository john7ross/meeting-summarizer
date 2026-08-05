"""Microphone recorder — capture a meeting live, then feed it to the pipeline.

Records straight to 16 kHz mono 16-bit WAV, which is exactly what every ASR
engine wants: no codec dependency, no re-encode step, and the file drops into
the normal add-file flow (trim dialog -> queue) like any other recording.

``WavWriter`` is deliberately Qt-free so the file-format logic is unit-testable
without a microphone; ``AudioRecorder`` wraps it with Qt's audio capture.
"""
from __future__ import annotations

import audioop
import time
import wave
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2          # bytes (Int16)


class WavWriter:
    """Incremental 16 kHz mono 16-bit WAV writer (Qt-free, testable)."""

    def __init__(self, path, rate: int = SAMPLE_RATE, channels: int = CHANNELS,
                 width: int = SAMPLE_WIDTH):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(self.path, "wb")
        self._wav.setnchannels(channels)
        self._wav.setsampwidth(width)
        self._wav.setframerate(rate)
        self._rate, self._width, self._channels = rate, width, channels
        self._frames = 0
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        self._wav.writeframes(data)
        self._frames += len(data) // (self._width * self._channels)

    @property
    def seconds(self) -> float:
        return self._frames / float(self._rate)

    def close(self) -> str:
        if not self._closed:
            self._closed = True
            self._wav.close()
        return self.path


def peak_level(data: bytes, width: int = SAMPLE_WIDTH) -> float:
    """Peak amplitude of a PCM chunk as 0.0..1.0 (for the level meter)."""
    if not data:
        return 0.0
    try:
        return min(1.0, audioop.max(data, width) / 32768.0)
    except Exception:      # noqa: BLE001 — a meter must never break recording
        return 0.0


def input_devices() -> list:
    """Available microphones; the system default first."""
    default = QMediaDevices.defaultAudioInput()
    devices = list(QMediaDevices.audioInputs())
    devices.sort(key=lambda d: d != default)     # default first
    return devices


def default_format() -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


class AudioRecorder(QObject):
    """Start/pause/stop microphone capture into a WAV file."""

    level = Signal(float)        # 0..1 peak, for the meter
    tick = Signal(float)         # seconds recorded
    finished = Signal(str)       # written file path
    failed = Signal(str)         # capture stopped because the device failed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Optional[QAudioSource] = None
        self._io = None
        self._writer: Optional[WavWriter] = None
        self._paused = False
        self._session_id = 0
        self._failure_reported = False
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._on_tick)

    @property
    def recording(self) -> bool:
        return self._writer is not None

    def start(self, path, device=None) -> None:
        if self.recording:
            return
        device = device or QMediaDevices.defaultAudioInput()
        fmt = default_format()
        if not device.isNull() and not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()       # fall back; still written correctly
        self._writer = WavWriter(path,
                                 rate=fmt.sampleRate(),
                                 channels=fmt.channelCount(),
                                 width=max(1, fmt.bytesPerSample()))
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None or self._source.error().value != QAudio.Error.NoError.value:
            error = self._source.error().name
            self._discard_empty(self.stop())
            raise RuntimeError(f"QAudioSource could not start ({error})")
        # Connect only after start() has returned.  Windows can emit transient
        # state changes synchronously inside start(); handling them earlier
        # races with initialisation and can close the writer twice.
        self._source.stateChanged.connect(self._on_source_state)
        self._io.readyRead.connect(self._on_ready)
        self._paused = False
        self._failure_reported = False
        self._session_id += 1
        session_id = self._session_id
        self._timer.start()
        # Some Windows devices accept start() but never deliver a single byte.
        # Without this watchdog the UI falsely says "recording" forever at 0:00.
        QTimer.singleShot(3000, lambda: self._check_capture_started(session_id))

    def _on_ready(self) -> None:
        if self._io is None or self._writer is None or self._paused:
            if self._io is not None and self._paused:
                self._io.readAll()               # drain, don't record while paused
            return
        data = bytes(self._io.readAll())
        self._writer.write(data)
        self.level.emit(peak_level(data, self._writer._width))

    def _on_tick(self) -> None:
        if self._writer is not None:
            self.tick.emit(self._writer.seconds)

    def _on_source_state(self, state: QAudio.State) -> None:
        if (self.recording and state == QAudio.State.StoppedState
                and self._source is not None
                and self._source.error().value != QAudio.Error.NoError.value):
            self._fail_capture(
                f"audio input stopped ({self._source.error().name})")

    def _check_capture_started(self, session_id: int) -> None:
        if session_id != self._session_id or not self.recording or self._paused:
            return
        if self._writer is not None and self._writer.seconds <= 0:
            state = self._source.state().name if self._source is not None else "no source"
            error = self._source.error().name if self._source is not None else "unknown"
            self._fail_capture(
                f"microphone delivered no audio data in 3 seconds "
                f"(state={state}, error={error})")

    def _fail_capture(self, message: str) -> None:
        if self._failure_reported:
            return
        self._failure_reported = True
        self._discard_empty(self.stop())
        self.failed.emit(message)

    @staticmethod
    def _discard_empty(path: str) -> None:
        """Remove the header-only WAV created by a device that never captured."""
        if not path:
            return
        try:
            candidate = Path(path)
            if candidate.is_file() and candidate.stat().st_size <= 44:
                candidate.unlink()
        except OSError:
            pass

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    @property
    def paused(self) -> bool:
        return self._paused

    def stop(self) -> str:
        """Stop capture, finalise the WAV, return its path ('' if not recording)."""
        if not self.recording:
            return ""
        self._session_id += 1
        self._timer.stop()
        # Clear the live fields before source.stop(): stateChanged may be
        # delivered synchronously and must observe recording == False.
        writer, source = self._writer, self._source
        self._writer, self._source, self._io = None, None, None
        try:
            if source is not None:
                source.stop()
        except Exception:      # noqa: BLE001
            pass
        path = writer.close()
        self.finished.emit(path)
        return path


def default_recording_path(root) -> Path:
    """recordings/Запись YYYY-MM-DD HH-MM-SS.wav — sorts chronologically and the
    name carries the date into the artifact folder downstream."""
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    return Path(root) / "recordings" / f"Запись {stamp}.wav"
