"""System-audio (WASAPI loopback) capture — the other half of a meeting.

A microphone records the person sitting at the machine. On a call, everyone else
comes out of the speakers, and a mic-only recording of an online meeting is a
recording of one participant talking to themselves. This module captures what
the machine PLAYS, so the recording holds both sides.

Kept deliberately separate from ``recorder.py``:

* it is **optional**. ``soundcard`` is a small pure-Python wheel, but if it is
  missing — or the machine has no loopback-capable output — recording must still
  work exactly as before, mic only. Every entry point here reports availability
  instead of raising into the recorder.
* it has **no Qt**. Capture runs on a plain thread with a ring buffer, so it is
  testable without an event loop and cannot stall the UI.

The microphone stays the clock. This class only answers "give me N samples of
what was playing", padding with silence when the device is momentarily behind
and dropping the oldest audio when it runs ahead. Two independent capture
devices never tick at exactly the same rate, and over an hour that drift has to
be absorbed somewhere; absorbing it against the mic keeps the two channels of
the WAV aligned with each other, which is the only alignment anyone hears.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional, Tuple

SAMPLE_WIDTH = 2                # bytes (Int16), same as the recorder
BUFFER_SECONDS = 3.0            # ring-buffer cap; older system audio is dropped


def probe() -> Tuple[bool, str]:
    """``(available, reason)`` — never raises, so the caller can just ask.

    ``reason`` is a short machine-ish string ('no-package', 'no-device', 'ok',
    or an error message) that the UI turns into its own localised text.
    """
    try:
        import soundcard                                    # noqa: F401
    except Exception:                                       # noqa: BLE001
        return False, "no-package"
    try:
        import soundcard as sc
        speaker = sc.default_speaker()
        if speaker is None:
            return False, "no-device"
        device = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        if device is None:
            return False, "no-device"
        return True, "ok"
    except Exception as exc:                                # noqa: BLE001
        return False, str(exc)


def default_output_name() -> str:
    """Human-readable name of the output that would be captured ('' if none)."""
    try:
        import soundcard as sc
        speaker = sc.default_speaker()
        return str(speaker.name) if speaker is not None else ""
    except Exception:                                       # noqa: BLE001
        return ""


class SystemAudioCapture:
    """Background WASAPI loopback capture with a bounded ring buffer."""

    def __init__(self, sample_rate: int = 16000, block_frames: int = 1024):
        self.sample_rate = int(sample_rate)
        self.block_frames = int(block_frames)
        self._buffer = deque()                  # of bytes chunks
        self._buffered_bytes = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error = ""
        self._device_name = ""
        self._max_bytes = int(BUFFER_SECONDS * self.sample_rate) * SAMPLE_WIDTH

    # -- lifecycle -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str:
        """Why capture stopped, or '' — read after ``read()`` returns silence."""
        return self._error

    @property
    def device_name(self) -> str:
        return self._device_name

    def start(self) -> bool:
        """Begin capturing. Returns False (with ``error`` set) instead of raising:
        losing system audio must never abort a recording that can still capture
        the microphone."""
        if self.running:
            return True
        available, reason = probe()
        if not available:
            self._error = reason
            return False
        self._stop.clear()
        self._error = ""
        started = threading.Event()
        self._thread = threading.Thread(
            target=self._capture_loop, args=(started,), daemon=True,
            name="system-audio-capture")
        self._thread.start()
        # Wait for the device to actually open: reporting success before the
        # first block would hide a device that fails immediately.
        started.wait(timeout=5.0)
        return self.running and not self._error

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)

    # -- consumption -----------------------------------------------------
    def read(self, frames: int) -> bytes:
        """Exactly ``frames`` samples of mono int16, silence-padded if short."""
        want = max(0, int(frames)) * SAMPLE_WIDTH
        if want == 0:
            return b""
        out = bytearray()
        with self._lock:
            while self._buffer and len(out) < want:
                chunk = self._buffer.popleft()
                self._buffered_bytes -= len(chunk)
                need = want - len(out)
                if len(chunk) > need:
                    out += chunk[:need]
                    self._buffer.appendleft(chunk[need:])
                    self._buffered_bytes += len(chunk) - need
                else:
                    out += chunk
        if len(out) < want:
            out += b"\x00" * (want - len(out))
        return bytes(out)

    def drop(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._buffered_bytes = 0

    # -- capture thread --------------------------------------------------
    def _capture_loop(self, started: threading.Event) -> None:
        try:
            import numpy as np
            import soundcard as sc
            speaker = sc.default_speaker()
            self._device_name = str(speaker.name)
            device = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with device.recorder(samplerate=self.sample_rate, channels=1,
                                 blocksize=self.block_frames) as rec:
                started.set()
                while not self._stop.is_set():
                    block = rec.record(numframes=self.block_frames)
                    if block is None or len(block) == 0:
                        continue
                    mono = np.asarray(block).reshape(-1)
                    # float32 [-1, 1] -> int16, clipped: a loud desktop mixer can
                    # exceed 1.0 and would wrap to the opposite sign without this.
                    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
                    with self._lock:
                        self._buffer.append(pcm)
                        self._buffered_bytes += len(pcm)
                        while self._buffered_bytes > self._max_bytes and self._buffer:
                            dropped = self._buffer.popleft()
                            self._buffered_bytes -= len(dropped)
        except Exception as exc:                            # noqa: BLE001
            self._error = str(exc)
        finally:
            started.set()
