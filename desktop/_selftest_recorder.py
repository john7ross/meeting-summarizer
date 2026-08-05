"""Self-test for the microphone recorder core (app/core/recorder.py).

The WAV writing and level metering are Qt-free, so they are verified with
synthetic PCM — no microphone required. Device enumeration is checked too (it
must not raise even with no inputs). Run:
    backend\\python\\python.exe desktop\\_selftest_recorder.py
"""
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication            # noqa: E402
app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from app.core import recorder                          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


def tone(seconds: float, rate: int = 16000, amp: int = 12000) -> bytes:
    n = int(seconds * rate)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / rate)))
                    for i in range(n))


tmp = Path(tempfile.mkdtemp())

# -- WavWriter: real, playable WAV with correct parameters -------------
path = tmp / "rec.wav"
w = recorder.WavWriter(path)
w.write(tone(0.5))
check("seconds_after_half", abs(w.seconds - 0.5) < 0.01, f"{w.seconds:.3f}")
w.write(tone(0.5))
check("seconds_accumulate", abs(w.seconds - 1.0) < 0.01, f"{w.seconds:.3f}")
out = w.close()
check("file_written", Path(out).exists() and Path(out).stat().st_size > 1000)

with wave.open(out, "rb") as r:
    check("wav_rate", r.getframerate() == recorder.SAMPLE_RATE, str(r.getframerate()))
    check("wav_mono", r.getnchannels() == 1, str(r.getnchannels()))
    check("wav_16bit", r.getsampwidth() == 2, str(r.getsampwidth()))
    check("wav_duration", abs(r.getnframes() / r.getframerate() - 1.0) < 0.01,
          f"{r.getnframes() / r.getframerate():.3f}s")

# writing after close must be a no-op, not a crash
w.write(tone(0.1))
check("write_after_close_safe", True)

# -- level meter -------------------------------------------------------
check("level_silence", recorder.peak_level(b"\x00\x00" * 100) == 0.0)
loud = recorder.peak_level(struct.pack("<h", 32000) * 100)
check("level_loud", 0.9 < loud <= 1.0, f"{loud:.2f}")
quiet = recorder.peak_level(struct.pack("<h", 1000) * 100)
check("level_ordering", quiet < loud, f"{quiet:.2f} < {loud:.2f}")
check("level_empty_safe", recorder.peak_level(b"") == 0.0)

# -- device enumeration + format --------------------------------------
devs = recorder.input_devices()
check("devices_enumerate", isinstance(devs, list), f"{len(devs)} device(s)")
fmt = recorder.default_format()
check("format_16k_mono", fmt.sampleRate() == 16000 and fmt.channelCount() == 1)

# -- naming ------------------------------------------------------------
p = recorder.default_recording_path(tmp)
check("recording_path_dir", p.parent.name == "recordings", str(p.parent))
check("recording_path_wav", p.suffix == ".wav", p.name)

# -- recorder object lifecycle (no mic needed) -------------------------
rec = recorder.AudioRecorder()
check("not_recording_initially", rec.recording is False)
check("stop_when_idle_is_empty", rec.stop() == "")

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
