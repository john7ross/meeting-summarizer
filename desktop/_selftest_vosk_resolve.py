"""TODO #14e — vosk model resolution honours the configured model NAME.

The engine-aware UI stores a concrete vosk model name in whisperModel; the vosk
adapter must load THAT model (not always the language default). Verified against
the real on-disk models, plus the legacy-fallback and not-downloaded paths.

Run:
    backend\\python\\python.exe desktop\\_selftest_vosk_resolve.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from processing.engines.vosk_engine import _resolve_vosk_model

PASS, FAIL = [], []
def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if (detail and not ok) else ""))

# explicit concrete names are honoured and resolve to the matching dir on disk
p, n = _resolve_vosk_model("vosk-model-small-ru-0.22", "ru")
check("explicit_small_ru", n == "vosk-model-small-ru-0.22" and bool(p)
      and p.replace("\\", "/").endswith("vosk_models/vosk-model-small-ru-0.22"), str((n, p)))
p, n = _resolve_vosk_model("vosk-model-ru-0.22", "ru")
check("explicit_large_ru", n == "vosk-model-ru-0.22" and p.endswith("vosk-model-ru-0.22"))
p, n = _resolve_vosk_model("vosk-model-en-us-0.22", "en")
check("explicit_large_en", n == "vosk-model-en-us-0.22" and bool(p))

# explicit model wins over the language argument (cross-language pick honoured)
p, n = _resolve_vosk_model("vosk-model-small-en-us-0.15", "ru")
check("explicit_overrides_language", n == "vosk-model-small-en-us-0.15" and bool(p))

# legacy callers: a whisper size or blank -> small model for the language
p, n = _resolve_vosk_model("small", "ru")
check("legacy_size_ru_fallback", n == "vosk-model-small-ru-0.22" and bool(p), str((n, p)))
p, n = _resolve_vosk_model("", "en")
check("blank_en_fallback", n == "vosk-model-small-en-us-0.15" and bool(p), str((n, p)))
p, n = _resolve_vosk_model("medium", "en")
check("legacy_unknown_en_fallback", n == "vosk-model-small-en-us-0.15" and bool(p))

# a known model that is NOT on disk -> clear error (no silent wrong model)
empty = tempfile.mkdtemp()
raised = False
try:
    _resolve_vosk_model("vosk-model-small-ru-0.22", "ru", res_dir=empty)
except Exception:
    raised = True
check("missing_known_model_raises", raised)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
