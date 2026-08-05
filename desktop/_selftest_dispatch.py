"""TODO #14c — registry-driven engine dispatch in processor.py.

No real transcription is run: the engine adapter map is checked against the
registry, resolve_engine's selection logic is exercised, and the per-engine
argument wiring is verified by monkeypatching the transcribe functions and
inspecting what each adapter forwards.

Run:
    backend\\python\\python.exe desktop\\_selftest_dispatch.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import engines_registry as reg
import processor

PASS, FAIL = [], []
def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if (detail and not ok) else ""))

# the adapter map must equal exactly the registry's IMPLEMENTED engines
adapters = processor._build_adapters("T", "M", "C")
impl = {e for e in reg.ENGINES if reg.is_implemented(e)}
check("adapters_match_implemented", set(adapters) == impl, f"{set(adapters)} vs {impl}")

avail = set(adapters)
for eid in sorted(avail):
    check(f"resolve_self[{eid}]", processor.resolve_engine(eid, avail) == eid)
check("resolve_unknown_defaults_whisper",
      processor.resolve_engine("totally-bogus", avail) == "whisper")

raised = False
reg.ENGINES["__fake_unimpl__"] = {"implemented": False, "models": {}}   # declared, no adapter
try:
    processor.resolve_engine("__fake_unimpl__", avail)
except RuntimeError:
    raised = True
finally:
    reg.ENGINES.pop("__fake_unimpl__", None)
check("resolve_declared_unimplemented_raises", raised)

# full per-engine wiring: 6 common args + the right trailing args, right fn
calls = {}
def fake(name):
    def f(*args):
        calls[name] = args
        return f"OUT::{name}"
    return f

processor.transcribe_audio_openai_whisper = fake("whisper")
processor.transcribe_audio_faster_whisper = fake("faster")
processor.transcribe_audio_whisperx = fake("whisperx")
processor.transcribe_audio_vosk = fake("vosk")
processor.transcribe_audio_sherpa_onnx = fake("sherpa")
processor.transcribe_audio_whispercpp = fake("whispercpp")
processor.transcribe_audio_funasr = fake("funasr")
processor.transcribe_audio_sherpa_extra = fake("extra")
ad = processor._build_adapters("TRACER", "MISS", "CUDA", "HINT", "pyannote", "TOK")
common = ("chunks", "model", "lang", "dev", "out", "base")

# whisper + faster get the initial-prompt hint; whisperx gets diarization backend + hf token
check("wire_whisper", ad["whisper"](*common) == "OUT::whisper"
      and calls["whisper"] == common + ("TRACER", "MISS", "HINT"), str(calls.get("whisper")))
check("wire_faster", ad["faster-whisper"](*common) == "OUT::faster"
      and calls["faster"] == common + ("TRACER", "MISS", "CUDA", "HINT"), str(calls.get("faster")))
check("wire_whisperx", ad["whisperx"](*common) == "OUT::whisperx"
      and calls["whisperx"] == common + ("TRACER", "MISS", "pyannote", "TOK"), str(calls.get("whisperx")))
check("wire_vosk", ad["vosk"](*common) == "OUT::vosk"
      and calls["vosk"] == common + ("MISS",), str(calls.get("vosk")))
check("wire_sherpa", ad["sherpa-onnx"](*common) == "OUT::sherpa"
      and calls["sherpa"] == common + ("MISS",), str(calls.get("sherpa")))
check("wire_whispercpp", ad["whisper-cpp"](*common) == "OUT::whispercpp"
      and calls["whispercpp"] == common + ("MISS",), str(calls.get("whispercpp")))
check("wire_funasr", ad["funasr"](*common) == "OUT::funasr"
      and calls["funasr"] == common + ("MISS",), str(calls.get("funasr")))
check("wire_sherpa_extra", ad["sherpa-extra"](*common) == "OUT::extra"
      and calls["extra"] == common + ("MISS",), str(calls.get("extra")))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
