"""Live end-to-end check for the sherpa-onnx adapter (closes TODO #14d).

Runs the real transcribe adapter against the model's own bundled test_wavs
(real speech), using the installed sherpa-onnx package and the downloaded model.
Prints the produced transcript. Exits 2 if the model isn't on disk yet.

Run:
    backend\\python\\python.exe desktop\\_livetest_sherpa.py [model_name]
"""
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import engines_registry as reg
from processing.engines.sherpa_onnx_engine import transcribe_audio_sherpa_onnx

def _missing(name, pkgs, err):
    return f"missing dependency {name}: pip install {' '.join(pkgs)} ({err})"

name = sys.argv[1] if len(sys.argv) > 1 else "sherpa-onnx-small-zipformer-ru-2024-09-18"
model_dir = reg.resolve_model_path("sherpa-onnx", name)
if not model_dir:
    print("MODEL_NOT_READY", name)
    sys.exit(2)

wavs = sorted(glob.glob(os.path.join(model_dir, "test_wavs", "*.wav")))
if not wavs:
    print("NO_TEST_WAVS in", model_dir)
    sys.exit(3)

# the adapter deletes each chunk after processing -> copy test wavs to a temp dir
out = tempfile.mkdtemp()
chunks = []
for i, w in enumerate(wavs[:3]):
    dst = os.path.join(out, f"chunk{i}.wav")
    shutil.copy(w, dst)
    chunks.append(dst)

print("MODEL_DIR", model_dir)
print("CHUNKS", [os.path.basename(w) for w in wavs[:3]])
path = transcribe_audio_sherpa_onnx(chunks, name, "ru", "cpu", out, "live", _missing)
print("RAW_PATH", path)
print("----- TRANSCRIPT -----")
with open(path, encoding="utf-8") as f:
    print(f.read())
print("----- END -----")
