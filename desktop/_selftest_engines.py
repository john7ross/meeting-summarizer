"""Validates engines_registry against the REAL model layout on disk.

Availability expectations are derived from the FILESYSTEM where optional models
come and go (e.g. faster-whisper tiny); only the always-bundled ones are asserted
as present:
  whisper: tiny.pt, medium.pt
  faster-whisper: medium dir (others checked against disk, not hardcoded)
  vosk: ru/en large+small in vosk_models; ru-0.42/ru-0.10/en-lgraph in 'vosk - more models'

Run:
    backend\\python\\python.exe desktop\\_selftest_engines.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import engines_registry as reg
from whisperx_patch import whisperx_vad_safe_globals
from desktop.packaging import build as packaging

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── whisper: present vs absent on disk ────────────────────────────────────────
check("whisper_tiny_available",   reg.is_available("whisper", "tiny"))
check("whisper_medium_available", reg.is_available("whisper", "medium"))
check("whisper_small_absent",     not reg.is_available("whisper", "small"))
check("whisper_large_absent",     not reg.is_available("whisper", "large"))
check("whisper_multilingual_list",
      reg.list_models("whisper", "ru") == ["tiny", "base", "small", "medium", "large"])
check("whisper_default_medium",   reg.default_model("whisper") == "medium")

# ── faster-whisper ────────────────────────────────────────────────────────────
check("faster_medium_available",  reg.is_available("faster-whisper", "medium"))


def _fw_on_disk(model: str) -> bool:
    """Is a faster-whisper model actually present? (HF snapshot dir layout)"""
    d = ROOT / "resources" / "whisper_models" / f"models--Systran--faster-whisper-{model}"
    return d.is_dir()


# Availability must track the FILESYSTEM, not a snapshot of which optional models
# happened to be downloaded when this test was written (tiny comes and goes).
check("faster_availability_matches_disk",
      reg.is_available("faster-whisper", "tiny") == _fw_on_disk("tiny"),
      f"registry={reg.is_available('faster-whisper', 'tiny')} disk={_fw_on_disk('tiny')}")
check("faster_unknown_absent",
      not reg.is_available("faster-whisper", "definitely-not-a-real-model"))

# ── whisperx mirrors faster-whisper (no own model) ───────────────────────────
check("whisperx_uses_faster_models",
      reg.list_models("whisperx") == reg.list_models("faster-whisper"))
check("whisperx_medium_available", reg.is_available("whisperx", "medium"))
check("whisperx_resolves_to_faster_path",
      reg.resolve_model_path("whisperx", "medium") ==
      reg.resolve_model_path("faster-whisper", "medium"))

# ── vosk: default set present, language filtering ─────────────────────────────
for mid in ("vosk-model-ru-0.22", "vosk-model-small-ru-0.22",
            "vosk-model-en-us-0.22", "vosk-model-small-en-us-0.15"):
    check(f"vosk_available[{mid}]", reg.is_available("vosk", mid))
ru_default = reg.list_models("vosk", "ru")
check("vosk_ru_list_default_only",
      set(ru_default) == {"vosk-model-ru-0.22", "vosk-model-small-ru-0.22"},
      detail=str(ru_default))
en_default = reg.list_models("vosk", "en")
check("vosk_en_list_default_only",
      set(en_default) == {"vosk-model-en-us-0.22", "vosk-model-small-en-us-0.15"},
      detail=str(en_default))

# ── 'implemented' flag gates download/selection (TODO #14 contract) ───────────
for eng in ("whisper", "faster-whisper", "whisperx", "vosk", "sherpa-onnx",
            "whisper-cpp", "funasr", "sherpa-extra"):
    check(f"implemented[{eng}]", reg.is_implemented(eng))
check("sherpa_declared", "sherpa-onnx" in reg.ENGINES)
check("sherpa_has_4_models", len(reg.list_models("sherpa-onnx")) == 4,
      str(reg.list_models("sherpa-onnx")))
check("sherpa_models_not_on_disk",
      not reg.is_available("sherpa-onnx", "sherpa-onnx-zipformer-en-2023-04-01"))
check("sherpa_unknown_model_none",
      reg.resolve_model_path("sherpa-onnx", "nope") is None)

# ── whisper.cpp (ggml, multilingual, TODO #14f) ──────────────────────────────
check("whispercpp_declared", "whisper-cpp" in reg.ENGINES)
check("whispercpp_multilingual_list",
      reg.list_models("whisper-cpp", "ru") == ["tiny", "base", "small", "medium", "large"])
check("whispercpp_default_base", reg.default_model("whisper-cpp") == "base")
check("whispercpp_unknown_model_none",
      reg.resolve_model_path("whisper-cpp", "nope") is None)
check("whispercpp_intended_bin_path",
      (reg.intended_path("whisper-cpp", "base") or "").replace("\\", "/").endswith(
          "resources/whispercpp_models/ggml-base.bin"))
check("whispercpp_url_pattern",
      reg.whispercpp_download_url("base") ==
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin")

# ── FunASR (SenseVoice/Paraformer via sherpa-onnx, EN-only, TODO #14f) ────────
check("funasr_declared", "funasr" in reg.ENGINES)
check("funasr_en_has_2_models", len(reg.list_models("funasr", "en")) == 2,
      str(reg.list_models("funasr", "en")))
check("funasr_ru_list_empty", reg.list_models("funasr", "ru") == [],
      str(reg.list_models("funasr", "ru")))   # EN-only: hidden for Russian
check("funasr_default_sensevoice",
      reg.default_model("funasr") == "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17")
check("funasr_intended_dir_path",
      (reg.intended_path("funasr", "sherpa-onnx-paraformer-en-2024-03-09") or "")
      .replace("\\", "/").endswith(
          "resources/funasr_models/sherpa-onnx-paraformer-en-2024-03-09"))
check("funasr_unknown_model_none",
      reg.resolve_model_path("funasr", "nope") is None)
check("funasr_url_reuses_k2fsa",
      reg.sherpa_download_url("sherpa-onnx-paraformer-en-2024-03-09") ==
      "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
      "sherpa-onnx-paraformer-en-2024-03-09.tar.bz2")

# ── sherpa-extra: optional community models, NOT bundled (TODO #14g) ──────────
check("extra_declared", "sherpa-extra" in reg.ENGINES)
check("extra_flag_true", reg.ENGINES["sherpa-extra"].get("extra") is True)
check("extra_ru_has_gigaam",
      "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19" in reg.list_models("sherpa-extra", "ru"))
check("extra_en_has_moonshine",
      "sherpa-onnx-moonshine-tiny-en-int8" in reg.list_models("sherpa-extra", "en"))
check("extra_ru_no_moonshine",
      "sherpa-onnx-moonshine-tiny-en-int8" not in reg.list_models("sherpa-extra", "ru"))
check("extra_intended_dir",
      (reg.intended_path("sherpa-extra", "sherpa-onnx-moonshine-tiny-en-int8") or "")
      .replace("\\", "/").endswith("resources/sherpa_extra_models/sherpa-onnx-moonshine-tiny-en-int8"))
# the whole point: extra models must NOT appear in ANY build variant (not even full)
extra_ids = set(reg.ENGINES["sherpa-extra"]["models"])
in_variants = {mod for pairs in reg.VARIANTS.values() for eng, mod in pairs if eng == "sherpa-extra"}
check("extra_not_in_any_variant", not in_variants and "sherpa-extra" not in
      {eng for pairs in reg.VARIANTS.values() for eng, _ in pairs}, str(in_variants))

# ── the pipeline must accept every engine the registry ships ────────────────
# This list was hardcoded once and went stale: sherpa-onnx, whisper.cpp and
# FunASR shipped and were offered in BOTH front-ends' settings, but every
# meeting using one died at extraction with "Unknown engine". The command
# builder is shared by the desktop pipeline and the server worker, so a stale
# list breaks the whole product, not one screen.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.backend import transcription as _T                 # noqa: E402

_selectable = [e for e, spec in reg.ENGINES.items()
               if reg.is_implemented(e) and not spec.get("extra")]
check("pipeline_accepts_every_shipped_engine",
      set(_selectable) <= set(_T.ENGINES),
      str(sorted(set(_selectable) - set(_T.ENGINES))))
check("pipeline_offers_nothing_extra",
      set(_T.ENGINES) <= set(_selectable),
      str(sorted(set(_T.ENGINES) - set(_selectable))))
for _eng in _selectable:
    try:
        _cmd = _T.build_command("v.mkv", "out", engine=_eng)
        check(f"build_command_accepts_{_eng}", _eng in _cmd)
    except Exception as _exc:                                # noqa: BLE001
        check(f"build_command_accepts_{_eng}", False, str(_exc)[:70])
try:
    _T.build_command("v.mkv", "out", engine="sherpa-extra")
    check("download_only_pack_is_not_selectable", False, "accepted")
except ValueError:
    check("download_only_pack_is_not_selectable", True)
try:
    _T.build_command("v.mkv", "out", engine="definitely-not-an-engine")
    check("unknown_engine_still_refused", False, "accepted")
except ValueError:
    check("unknown_engine_still_refused", True)

# ── every build variant ships every (own-model) engine ───────────────────────
OWN_MODEL_ENGINES = {"whisper", "faster-whisper", "vosk"}   # whisperx rides faster-whisper
for variant, pairs in reg.VARIANTS.items():
    engines_in = {eng for eng, _ in pairs}
    check(f"variant_all_engines[{variant}]",
          OWN_MODEL_ENGINES.issubset(engines_in), detail=str(sorted(engines_in)))
check("minimal_uses_whisper_small",
      ("whisper", "small") in reg.VARIANTS["minimal"])

# ── negative + helpers ────────────────────────────────────────────────────────
check("bogus_model_none", reg.resolve_model_path("vosk", "nope-9.9") is None)
check("bogus_engine_none", reg.resolve_model_path("sphinx", "x") is None)
check("vosk_default_small_ru", reg.default_model("vosk") == "vosk-model-small-ru-0.22")
check("vosk_url_pattern",
      reg.vosk_download_url("vosk-model-ru-0.22") ==
      "https://alphacephei.com/vosk/models/vosk-model-ru-0.22.zip")
check("res_dir_override_isolates",
      reg.resolve_model_path("whisper", "tiny", res_dir=str(ROOT / "no_such_dir")) is None)

# ── variants reference only known (engine, model) pairs ───────────────────────
bad = []
for variant, pairs in reg.VARIANTS.items():
    for eng, mod in pairs:
        e = reg.ENGINES.get(eng)
        models = (reg.ENGINES[e["uses"]]["models"] if e and e.get("uses")
                  else (e or {}).get("models") or {})
        if mod not in models:
            bad.append(f"{variant}:{eng}/{mod}")
check("variants_reference_known_models", not bad, detail=str(bad))

# The min package must be usable with its default WhisperX diarization setting,
# without an undiscoverable manual model download.
min_items, min_missing = packaging.collect_min_safety(ROOT)
min_names = {arc.replace("\\", "/") for _, arc in min_items}
check("min_bundles_default_diarization",
      any(name.startswith("resources/diarization_models/") for name in min_names)
      and "diarization/offline-default" not in min_missing,
      detail=str(min_missing))

# PyTorch 2.6+ must load WhisperX's legacy VAD checkpoint without globally
# disabling weights-only safety. If the checkpoint is cached, prove our
# explicit allowlist covers every serialized non-default global.
try:
    import torch
    vad = Path.home() / ".cache" / "torch" / "whisperx-vad-segmentation.bin"
    allowed = {
        f"{obj.__module__}.{obj.__qualname__}"
        for obj in whisperx_vad_safe_globals()
    }
    unsafe = (set(torch.serialization.get_unsafe_globals_in_checkpoint(vad))
              if vad.is_file() else set())
    check("whisperx_vad_safe_allowlist", unsafe <= allowed,
          detail=str(sorted(unsafe - allowed)))
except Exception as exc:
    check("whisperx_vad_safe_allowlist", False, detail=str(exc))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
