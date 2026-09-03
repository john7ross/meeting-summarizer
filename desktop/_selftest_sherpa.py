"""TODO #14d — sherpa-onnx model resolution + file picking + download plan.

Hermetic: builds a fake sherpa transducer model dir (empty files) under a temp
resources root and checks resolution, the int8-preference of _pick, the
registry availability/intended-path, and the download plan source URL. No
network and no sherpa-onnx package required (transcription itself is verified
later, live, after `pip install sherpa-onnx`).

Run:
    backend\\python\\python.exe desktop\\_selftest_sherpa.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import engines_registry as reg
from processing.engines.sherpa_onnx_engine import (
    _pick, _resolve_sherpa_model, _sample_windows)
from download_model import plan

PASS, FAIL = [], []
def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if (detail and not ok) else ""))

RU = "sherpa-onnx-small-zipformer-ru-2024-09-18"
EN = "sherpa-onnx-zipformer-small-en-2023-06-26"

# build a fake on-disk model: tokens.txt + encoder(int8 + fp32)/decoder/joiner
res = tempfile.mkdtemp()
mdir = os.path.join(res, "sherpa_models", RU)
os.makedirs(mdir)
for fn in ("tokens.txt",
           "encoder-epoch-99-avg-1.onnx", "encoder-epoch-99-avg-1.int8.onnx",
           "decoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.onnx"):
    open(os.path.join(mdir, fn), "w").close()

# registry sees it as available; intended_path points at the right place
check("registry_available", reg.is_available("sherpa-onnx", RU, res_dir=res))
check("registry_intended_path",
      reg.intended_path("sherpa-onnx", RU, res_dir=res).replace("\\", "/").endswith(
          f"sherpa_models/{RU}"))
check("registry_missing_not_available",
      not reg.is_available("sherpa-onnx", EN, res_dir=res))   # EN not on disk

# resolution honours the explicit known name
p, n = _resolve_sherpa_model(RU, "ru", res_dir=res)
check("resolve_explicit", n == RU and p == mdir, str((n, p)))

# legacy size / blank -> small model for the language
_, n = _resolve_sherpa_model("small", "ru", res_dir=res)
check("resolve_legacy_ru", n == RU)
# en small isn't on disk here -> resolving the en default must raise
raised = False
try:
    _resolve_sherpa_model("", "en", res_dir=res)
except Exception:
    raised = True
check("resolve_en_default_missing_raises", raised)

# known model not downloaded -> raises
raised = False
try:
    _resolve_sherpa_model(EN, "en", res_dir=res)
except Exception:
    raised = True
check("resolve_known_missing_raises", raised)

# _pick prefers the non-int8 onnx for each role
enc = _pick(mdir, "encoder")
check("pick_prefers_fp32", enc.endswith("encoder-epoch-99-avg-1.onnx"), enc)
check("pick_decoder", _pick(mdir, "decoder").endswith("decoder-epoch-99-avg-1.onnx"))
check("pick_joiner", _pick(mdir, "joiner").endswith("joiner-epoch-99-avg-1.onnx"))

# The fixed-shape Russian Zipformer cannot decode a normal 10-minute processor
# chunk directly. Long audio is divided into <=18-second pieces, preferring the
# quietest point near each boundary.
import numpy as np
sr = 16000
audio = np.ones(sr * 50, dtype=np.float32)
audio[sr * 16:sr * 17] = 0.0
windows = list(_sample_windows(audio, sr))
check("long_audio_windowed",
      len(windows) >= 3 and all(len(w) <= sr * 18 for _, w in windows),
      str([(round(start / sr, 2), round(len(w) / sr, 2)) for start, w in windows]))
first_end = windows[0][0] / sr + len(windows[0][1]) / sr
check("window_prefers_quiet_boundary", 16.0 <= first_end <= 17.1, str(first_end))

# download plan: method + verified release URL, no network
pl = plan("sherpa-onnx", RU, res_dir=res)
check("plan_method", pl["method"] == "sherpa_targz", str(pl["method"]))
check("plan_source_url",
      pl["source"] == f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{RU}.tar.bz2",
      str(pl["source"]))
check("plan_already_true", pl["already"] is True)   # we created it on disk

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
