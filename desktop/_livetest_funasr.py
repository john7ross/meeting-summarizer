"""Live end-to-end check for the FunASR adapter (closes TODO #14f FunASR, EN).

Runs the real transcribe adapter (SenseVoice/Paraformer via the sherpa-onnx
runtime) against a downloaded model, using the model's own bundled English
test_wav. Prints the produced transcript. Exits 2 if the model isn't on disk.

Run:
    backend\\python\\python.exe desktop\\_livetest_funasr.py [model_name]
"""
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import engines_registry as reg
from processing.engines.funasr_engine import transcribe_audio_funasr

def _missing(name, pkgs, err):
    return f"missing dependency {name}: pip install {' '.join(pkgs)} ({err})"

name = sys.argv[1] if len(sys.argv) > 1 else "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
model_dir = reg.resolve_model_path("funasr", name)
if not model_dir:
    print("MODEL_NOT_READY", name)
    sys.exit(2)

# prefer an English test wav bundled with the model
wavs = sorted(glob.glob(os.path.join(model_dir, "test_wavs", "*.wav")))
en = [w for w in wavs if "en" in os.path.basename(w).lower()] or wavs
if not en:
    print("NO_TEST_WAVS in", model_dir)
    sys.exit(3)

out = tempfile.mkdtemp()
dst = os.path.join(out, "chunk0.wav")
shutil.copy(en[0], dst)

print("MODEL_DIR", model_dir)
print("WAV", os.path.basename(en[0]))
path = transcribe_audio_funasr([dst], name, "en", "cpu", out, "live", _missing)
print("RAW_PATH", path)
print("----- TRANSCRIPT -----")
with open(path, encoding="utf-8") as f:
    txt = f.read()
print(txt)
print("----- END ----- CHARS", len(txt))
