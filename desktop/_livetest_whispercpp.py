"""Live end-to-end check for the whisper.cpp adapter (closes TODO #14f whisper.cpp).

Runs the real transcribe adapter against a downloaded ggml model, using real
speech WAVs (reuses the sherpa RU model's bundled 16 kHz mono test_wavs). Prints
the produced transcript. Exits 2 if the ggml model isn't on disk yet.

Run:
    backend\\python\\python.exe desktop\\_livetest_whispercpp.py [size]
"""
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import engines_registry as reg
from processing.engines.whispercpp_engine import transcribe_audio_whispercpp

def _missing(name, pkgs, err):
    return f"missing dependency {name}: pip install {' '.join(pkgs)} ({err})"

size = sys.argv[1] if len(sys.argv) > 1 else "base"
model_path = reg.resolve_model_path("whisper-cpp", size)
if not model_path:
    print("MODEL_NOT_READY", size)
    sys.exit(2)

sherpa_dir = reg.resolve_model_path("sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18")
wavs = sorted(glob.glob(os.path.join(sherpa_dir or "", "test_wavs", "*.wav"))) if sherpa_dir else []
if not wavs:
    print("NO_TEST_WAVS (need the sherpa RU model on disk for sample audio)")
    sys.exit(3)

out = tempfile.mkdtemp()
chunks = []
for i, w in enumerate(wavs[:2]):
    dst = os.path.join(out, f"chunk{i}.wav")
    shutil.copy(w, dst)
    chunks.append(dst)

print("MODEL", model_path)
print("CHUNKS", [os.path.basename(w) for w in wavs[:2]])
path = transcribe_audio_whispercpp(chunks, size, "ru", "cpu", out, "live", _missing)
print("RAW_PATH", path)
print("----- TRANSCRIPT (unicode-escape) -----")
with open(path, encoding="utf-8") as f:
    txt = f.read()
print(txt.encode("unicode_escape").decode("ascii"))
cyr = sum(1 for c in txt if "Ѐ" <= c <= "ӿ")
print("----- END ----- CYRILLIC_CHARS", cyr)
