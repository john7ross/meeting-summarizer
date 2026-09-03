"""Meeting recorder — capture a meeting live, then feed it to the pipeline.

Records straight to 16 kHz 16-bit WAV, which is exactly what every ASR engine
wants: no codec dependency, no re-encode step, and the file drops into the
normal add-file flow (trim dialog -> queue) like any other recording.

Two sources, when the machine allows it. The microphone is the local
participant; system audio (WASAPI loopback, see ``loopback.py``) is everyone
else on the call. They are written as the two channels of ONE stereo WAV — mic
left, system right — rather than two files, because a single file keeps the
existing add-file flow untouched and the two halves permanently in sync. Both
downstream consumers (``processing/audio.py`` and ``backend/media.py``) already
force ``-ac 1``, so the batch transcript sees a correct downmix of the whole
conversation with no change on their side; the live path, which does look at the
channels, uses them to tell "me" from "them".

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

from .loopback import SystemAudioCapture

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


def interleave_stereo(left: bytes, right: bytes) -> bytes:
    """Two mono int16 streams -> one interleaved stereo stream (L, R, L, R...).

    Qt-free and length-tolerant on purpose: the two capture devices are
    independent, so the shorter side is padded with silence rather than
    truncating the other and shifting everything after it.
    """
    size = max(len(left), len(right))
    size -= size % SAMPLE_WIDTH
    left = left[:size].ljust(size, b"\x00")
    right = right[:size].ljust(size, b"\x00")
    out = bytearray(size * 2)
    out[0::4] = left[0::2]
    out[1::4] = left[1::2]
    out[2::4] = right[0::2]
    out[3::4] = right[1::2]
    return bytes(out)


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
    """Start/pause/stop capture into a WAV file (microphone, plus system audio)."""

    level = Signal(float)        # 0..1 peak, for the meter
    tick = Signal(float)         # seconds recorded
    finished = Signal(str)       # written file path
    failed = Signal(str)         # capture stopped because the device failed
    # Everything written to the WAV, as it is written: (pcm, channels, rate).
    # The live transcription worker listens here. It is a TAP, not a hand-off —
    # the file is written first and is unaffected by whatever the listener does,
    # so live transcription can lag, fail or be off entirely without ever
    # costing the recording a single sample.
    pcm = Signal(bytes, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Optional[QAudioSource] = None
        self._io = None
        self._writer: Optional[WavWriter] = None
        self._paused = False
        self._session_id = 0
        self._failure_reported = False
        self._system: Optional[SystemAudioCapture] = None
        self._system_error = ""
        self._mic_channels = CHANNELS
        self._rate = SAMPLE_RATE
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._on_tick)

    @property
    def recording(self) -> bool:
        return self._writer is not None

    @property
    def system_active(self) -> bool:
        """Whether system audio really made it into the recording."""
        return self._system is not None

    @property
    def system_error(self) -> str:
        """Why system audio is not being captured ('' when it is, or was not asked
        for). Surfaced to the user rather than silently recording half a call."""
        return self._system_error

    @property
    def channels(self) -> int:
        """Channels being written: 2 once system audio joined, else the mic's."""
        return 2 if self._system is not None else self._mic_channels

    def start(self, path, device=None, capture_system: bool = False) -> None:
        if self.recording:
            return
        device = device or QMediaDevices.defaultAudioInput()
        fmt = default_format()
        if not device.isNull() and not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()       # fall back; still written correctly
        self._mic_channels = fmt.channelCount()
        self._rate = fmt.sampleRate()
        self._system_error = ""
        self._system = None
        if capture_system:
            self._start_system_capture(fmt)
        self._writer = WavWriter(path,
                                 rate=fmt.sampleRate(),
                                 channels=self.channels,
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

    def _start_system_capture(self, fmt: QAudioFormat) -> None:
        """Attach loopback capture, or record explain why we are mic-only.

        Mixing needs 16-bit samples: the fallback microphone format can be
        float or 8-bit, and silently mixing those would corrupt both channels.
        A machine like that still records perfectly well from the mic — it just
        says so instead of pretending both sides are in the file.
        """
        if max(1, fmt.bytesPerSample()) != SAMPLE_WIDTH:
            self._system_error = "unsupported-format"
            return
        capture = SystemAudioCapture(sample_rate=fmt.sampleRate())
        if not capture.start():
            self._system_error = capture.error or "unavailable"
            return
        self._system = capture

    def _on_ready(self) -> None:
        if self._io is None or self._writer is None or self._paused:
            if self._io is not None and self._paused:
                self._io.readAll()               # drain, don't record while paused
                if self._system is not None:
                    self._system.drop()          # and don't record the room either
            return
        data = bytes(self._io.readAll())
        level_source = data
        if self._system is not None:
            data = self._mix_with_system(data)
        self._writer.write(data)
        # The meter follows the MICROPHONE, not the mix: a user checking whether
        # they are being heard must not be reassured by the other side's audio.
        self.level.emit(peak_level(level_source, SAMPLE_WIDTH
                                   if self._system is not None
                                   else self._writer._width))
        if data:
            self.pcm.emit(data, self.channels, self._rate)

    def _mix_with_system(self, mic: bytes) -> bytes:
        """Mic left, system right — the microphone drives the clock.

        Pulling exactly as many system samples as the mic just delivered is what
        keeps the two channels aligned over a long meeting: the loopback device
        runs on its own clock, and any drift is absorbed here (padded with
        silence when it is behind, oldest audio dropped when it runs ahead)
        instead of accumulating into an ever-growing offset between the halves.
        """
        mono = mic
        if self._mic_channels == 2:
            mono = audioop.tomono(mic, SAMPLE_WIDTH, 0.5, 0.5)
        frames = len(mono) // SAMPLE_WIDTH
        if self._system.error and not self._system_error:
            # It died mid-recording (device unplugged, output switched). Keep
            # recording the mic into the same file; the channel goes silent.
            self._system_error = self._system.error
        return interleave_stereo(mono, self._system.read(frames))

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
        system, self._system = self._system, None
        self._writer, self._source, self._io = None, None, None
        try:
            if source is not None:
                source.stop()
        except Exception:      # noqa: BLE001
            pass
        if system is not None:
            system.stop()
        path = writer.close()
        self.finished.emit(path)
        return path


def default_recording_path(root) -> Path:
    """recordings/Запись YYYY-MM-DD HH-MM-SS.wav — sorts chronologically and the
    name carries the date into the artifact folder downstream."""
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    return Path(root) / "recordings" / f"Запись {stamp}.wav"
