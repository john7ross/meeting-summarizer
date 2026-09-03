"""Live end-to-end check of the recorder's live mode — real devices, real model.

The self-tests drive the live path with synthetic PCM and a fake agent. This one
uses the machine itself: it starts a REAL recording (microphone + WASAPI
loopback), **plays speech through the default output** while it records, and then
checks that the played audio came back through the loopback channel, into the
stereo WAV, through the streaming worker and out as text.

That is the one thing a synthetic test cannot prove: that the two capture devices,
the mixer and the engine actually line up on this machine.

Needs: a microphone, a loopback-capable output device, the ``soundcard`` package,
and an offline model on disk (sherpa-onnx or vosk). Exits 2 if any is missing —
a skipped check must never look like a passed one.

Run (speakers will make noise for ~20 seconds):
    backend\\python\\python.exe desktop\\_livetest_live.py
"""
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtWidgets import QApplication                 # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

import engines_registry as reg                             # noqa: E402
from app.core.live_session import LiveSession              # noqa: E402
from app.core.loopback import probe as probe_loopback      # noqa: E402
from app.core.recorder import AudioRecorder                # noqa: E402

RATE = 16000


def bail(message: str) -> None:
    print(f"SKIP  {message}")
    raise SystemExit(2)


def pump(seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


available, reason = probe_loopback()
if not available:
    bail(f"no system-audio loopback on this machine ({reason})")

engine, model = "", ""
for candidate, name in (("sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18"),
                        ("vosk", "vosk-model-small-ru-0.22")):
    if reg.resolve_model_path(candidate, name):
        engine, model = candidate, name
        break
if not engine:
    bail("no Russian offline model on disk (sherpa-onnx or vosk)")

speech_dir = reg.resolve_model_path(
    "sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18")
wavs = sorted(Path(speech_dir, "test_wavs").glob("*.wav")) if speech_dir else []
if not wavs:
    bail("no speech sample to play (sherpa test_wavs missing)")

out_dir = ROOT / "recordings"
out_dir.mkdir(parents=True, exist_ok=True)
target = out_dir / f"livetest {time.strftime('%Y-%m-%d %H-%M-%S')}.wav"

recorder = AudioRecorder()
session = LiveSession()
segments = []
statuses = []
session.segment.connect(segments.append)
session.status.connect(lambda kind, msg: statuses.append((kind, msg)))
recorder.pcm.connect(lambda pcm, ch, rate: session.feed(pcm, ch, rate))

print(f"engine={engine} model={model}")
print("starting the recording (microphone + system audio)...")
try:
    recorder.start(target, capture_system=True)
except Exception as exc:                                   # noqa: BLE001
    bail(f"could not start recording: {exc}")

if not recorder.system_active:
    recorder.stop()
    bail(f"system audio did not attach ({recorder.system_error})")

started = session.start(
    {"liveEngine": engine, "liveModel": model, "transcriptionLanguage": "ru",
     "whisperDevice": "cpu", "liveSummary": False},
    target.with_suffix(""), channels=recorder.channels, sample_rate=RATE)
if not started:
    recorder.stop()
    bail(f"live session refused to start ({statuses[-1:]})")

print("waiting for the model to load...")
deadline = time.time() + 300
while time.time() < deadline and not any(k == "ready" for k, _ in statuses):
    pump(0.2)
if not any(k == "ready" for k, _ in statuses):
    session.stop()
    recorder.stop()
    bail(f"the worker never reported ready ({statuses[-2:]})")

print("playing speech through the default output device...")
import numpy as np                                          # noqa: E402
import soundcard as sc                                      # noqa: E402

speaker = sc.default_speaker()


def play_all() -> None:
    for path in wavs[:2]:
        with wave.open(str(path), "rb") as fh:
            frames = fh.readframes(fh.getnframes())
            rate = fh.getframerate()
        samples = (np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0)
        speaker.play(samples.reshape(-1, 1) * 0.9, samplerate=rate)
        time.sleep(1.2)                 # a pause the segmenter can end an utterance on


# Playback runs on its own thread and the Qt loop keeps spinning: ``speaker.play``
# blocks until the buffer drains, and blocking the event loop here would stop the
# microphone's readyRead from firing — the test would then measure its own stall
# instead of the capture path.
import threading                                            # noqa: E402
player = threading.Thread(target=play_all, daemon=True)
player.start()
while player.is_alive():
    pump(0.1)

print("draining...")
pump(3.0)
session.stop()
recorder.stop()
pump(1.0)

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


with wave.open(str(target), "rb") as fh:
    channels = fh.getnchannels()
    seconds = fh.getnframes() / float(fh.getframerate())
check("recording_is_stereo", channels == 2, f"{channels} channel(s)")
check("recording_has_duration", seconds > 5, f"{seconds:.1f}s")

check("live_produced_segments", bool(segments),
      f"{len(segments)} segment(s); statuses={statuses[-3:]}")
text = " | ".join(s.text for s in segments)
check("live_produced_words", any(len(s.text.split()) >= 2 for s in segments), text[:160])
check("played_audio_came_back_on_the_system_channel",
      any(s.source == "system" for s in segments),
      str([(s.source, s.text[:30]) for s in segments]))
transcript = Path(session.transcript_path)
check("transcript_written", transcript.is_file()
      and transcript.read_text(encoding="utf-8").strip() != "", str(transcript))

print("\n".join(results))
print(f"recording: {target}")
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
