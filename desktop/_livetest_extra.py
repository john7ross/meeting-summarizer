"""Live end-to-end check for the sherpa-extra adapter (TODO #14g).

Runs the real adapter (GigaAM via from_nemo_ctc / Moonshine via from_moonshine)
against a downloaded extra model, using the model's OWN bundled test_wavs (they
ship language-appropriate samples). Exits 2 if the model isn't on disk.

Run:
    backend\\python\\python.exe desktop\\_livetest_extra.py <model_name>
"""
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import engines_registry as reg
from processing.engines.sherpa_extra_engine import transcribe_audio_sherpa_extra

def _missing(name, pkgs, err):
    return f"missing dependency {name}: pip install {' '.join(pkgs)} ({err})"

name = sys.argv[1] if len(sys.argv) > 1 else reg.default_model("sherpa-extra")
lang = reg.ENGINES["sherpa-extra"]["models"].get(name, {}).get("lang", "en")
model_dir = reg.resolve_model_path("sherpa-extra", name)
if not model_dir:
    print("MODEL_NOT_READY", name)
    sys.exit(2)

wavs = sorted(glob.glob(os.path.join(model_dir, "test_wavs", "*.wav")))
if not wavs:
    print("NO_TEST_WAVS in", model_dir)
    sys.exit(3)

out = tempfile.mkdtemp()
dst = os.path.join(out, "chunk0.wav")
shutil.copy(wavs[0], dst)

print("MODEL_DIR", model_dir, "LANG", lang)
print("WAV", os.path.basename(wavs[0]))
path = transcribe_audio_sherpa_extra([dst], name, lang, "cpu", out, "live", _missing)
print("RAW_PATH", path)
print("----- TRANSCRIPT (unicode-escape) -----")
with open(path, encoding="utf-8") as f:
    txt = f.read()
print(txt.encode("unicode_escape").decode("ascii"))
print("----- END ----- CHARS", len(txt))
