"""Engine & model registry — the single source of truth for transcription
engines, their selectable models, where each model lives on disk, which language
it serves, and which build variant ships it.

Qt-free and dependency-free (stdlib only): it performs pure filesystem
availability checks, so it can be imported by the backend (``processor.py``,
``vosk_engine.py``) AND surfaced to the UI through a thin CLI/worker without
dragging heavy deps (torch/vosk/chromadb) anywhere.

Grounded in the REAL project (not the old Electron stub, which never ran Vosk):
  - settings keys: ``transcriptionEngine``, ``whisperModel``, ``transcriptionLanguage``;
    ``whisperModel`` holds a concrete value — a size for the whisper family, or a
    Vosk model directory name for the ``vosk`` engine (same scheme the old UI used).
  - on disk now: whisper ``tiny.pt`` / ``medium.pt``; faster-whisper ``medium``
    (HF snapshot dir); vosk ru/en large+small (extracted dirs) in ``vosk_models``,
    with extras in ``vosk - more models``.
  - whisperx has NO model of its own — it runs on faster-whisper models.

Adding a new OSS engine (sherpa-onnx, whisper.cpp, FunASR, ...):
  1) add an entry under ENGINES (models + on-disk layout + how to fetch);
  2) add a transcribe adapter module and register it in the processor dispatch;
  3) (optional) tag its models into VARIANTS.
No model needs to ship — an engine can be declared and fetched on demand.
"""
from __future__ import annotations

import glob
import os
from typing import Optional


# Model "kind" tells the resolver how a model maps to a path under <resources>:
#   "whisper_pt" -> whisper_models/<file>           (single OpenAI-Whisper .pt)
#   "faster_hf"  -> whisper_models/<dir>            (HF snapshot dir; also whisperx)
#   "vosk_dir"   -> vosk_models/<dir>                              (extracted)
#   "sherpa_transducer" -> sherpa_models/<dir>      (zipformer transducer: tokens
#                          + encoder/decoder/joiner .onnx)
#   "whispercpp_ggml" -> whispercpp_models/<file>  (single whisper.cpp ggml .bin)
#   "funasr_onnx" -> funasr_models/<dir>            (SenseVoice/Paraformer: model
#                    [.int8].onnx + tokens.txt; run via the sherpa-onnx runtime)
#   "sherpa_extra_dir" -> sherpa_extra_models/<dir> (OPTIONAL community models —
#                    GigaAM/Moonshine/…; validated per model_type; NOT bundled in
#                    any build variant, download-only)
_VOSK_DIRS = ("vosk_models",)
_SHERPA_DIR = "sherpa_models"
_WHISPERCPP_DIR = "whispercpp_models"
_FUNASR_DIR = "funasr_models"
_EXTRA_DIR = "sherpa_extra_models"


ENGINES = {
    "whisper": {
        "label": {"ru": "OpenAI Whisper", "en": "OpenAI Whisper"},
        "multilingual": True,          # one model serves every language
        "model_field": "size",         # whisperModel holds a size key
        "default_model": "medium",
        "download": "whisper_lib",     # fetched by the openai-whisper loader
        "implemented": True,           # transcribe adapter exists in processor.py
        "models": {
            "tiny":   {"kind": "whisper_pt", "file": "tiny.pt",     "approx_mb": 75},
            "base":   {"kind": "whisper_pt", "file": "base.pt",     "approx_mb": 142},
            "small":  {"kind": "whisper_pt", "file": "small.pt",    "approx_mb": 466},
            "medium": {"kind": "whisper_pt", "file": "medium.pt",   "approx_mb": 1500},
            "large":  {"kind": "whisper_pt", "file": "large-v3.pt", "approx_mb": 2900},
        },
    },
    "faster-whisper": {
        "label": {"ru": "Faster-Whisper (быстрее в 2–4 раза)",
                  "en": "Faster-Whisper (2-4x faster)"},
        "multilingual": True,
        "model_field": "size",
        "default_model": "medium",
        "download": "faster_lib",      # fetched by the faster-whisper / HF loader
        "implemented": True,
        "models": {
            "tiny":   {"kind": "faster_hf", "dir": "models--Systran--faster-whisper-tiny",     "approx_mb": 75},
            "base":   {"kind": "faster_hf", "dir": "models--Systran--faster-whisper-base",     "approx_mb": 142},
            "small":  {"kind": "faster_hf", "dir": "models--Systran--faster-whisper-small",    "approx_mb": 466},
            "medium": {"kind": "faster_hf", "dir": "models--Systran--faster-whisper-medium",   "approx_mb": 1500},
            "large":  {"kind": "faster_hf", "dir": "models--Systran--faster-whisper-large-v3", "approx_mb": 3000},
        },
    },
    "whisperx": {
        "label": {"ru": "WhisperX (быстрее всех, спикеры)",
                  "en": "WhisperX (fastest, speakers)"},
        "multilingual": True,
        "model_field": "size",
        "default_model": "medium",
        "download": "faster_lib",
        "implemented": True,
        "uses": "faster-whisper",      # NO own model: resolves against faster-whisper
        "models": None,                # mirrored from faster-whisper at resolve time
    },
    "vosk": {
        "label": {"ru": "Vosk (лёгкий, офлайн)", "en": "Vosk (lightweight, offline)"},
        "multilingual": False,         # each model is language-specific
        "model_field": "name",         # whisperModel holds a concrete vosk dir name
        "default_model": "vosk-model-small-ru-0.22",
        "download": "vosk_zip",        # alphacephei .zip -> extract
        "implemented": True,           # transcribe adapter exists (vosk_engine.py)
        "models": {
            "vosk-model-ru-0.22": {
                "kind": "vosk_dir", "dir": "vosk-model-ru-0.22", "lang": "ru", "tier": "max",
                "label": {"ru": "Русский — большая (0.22)", "en": "Russian — large (0.22)"}},
            "vosk-model-small-ru-0.22": {
                "kind": "vosk_dir", "dir": "vosk-model-small-ru-0.22", "lang": "ru", "tier": "min",
                "label": {"ru": "Русский — малая (0.22)", "en": "Russian — small (0.22)"}},
            "vosk-model-en-us-0.22": {
                "kind": "vosk_dir", "dir": "vosk-model-en-us-0.22", "lang": "en", "tier": "max",
                "label": {"ru": "Английский — большая (0.22)", "en": "English US — large (0.22)"}},
            "vosk-model-small-en-us-0.15": {
                "kind": "vosk_dir", "dir": "vosk-model-small-en-us-0.15", "lang": "en", "tier": "min",
                "label": {"ru": "Английский — малая (0.15)", "en": "English US — small (0.15)"}},
        },
    },
    "sherpa-onnx": {
        # Offline zipformer-transducer ASR via onnxruntime (TODO #14d). Models are
        # k2-fsa release archives; the adapter (sherpa_onnx_engine.py) loads them
        # with OfflineRecognizer.from_transducer. Models live under sherpa_models/.
        "label": {"ru": "sherpa-onnx (офлайн, ONNX)", "en": "sherpa-onnx (offline, ONNX)"},
        "multilingual": False,         # each model is language-specific
        "model_field": "name",
        "model_type": "transducer",
        "default_model": "sherpa-onnx-small-zipformer-ru-2024-09-18",
        "download": "sherpa_targz",    # github release .tar.bz2 -> extract
        "implemented": True,
        "models": {
            "sherpa-onnx-small-zipformer-ru-2024-09-18": {
                "kind": "sherpa_transducer", "dir": "sherpa-onnx-small-zipformer-ru-2024-09-18",
                "lang": "ru", "tier": "min",
                "label": {"ru": "Русский — малая (zipformer)", "en": "Russian — small (zipformer)"}},
            "sherpa-onnx-zipformer-ru-2024-09-18": {
                "kind": "sherpa_transducer", "dir": "sherpa-onnx-zipformer-ru-2024-09-18",
                "lang": "ru", "tier": "max",
                "label": {"ru": "Русский — большая (zipformer)", "en": "Russian — large (zipformer)"}},
            "sherpa-onnx-zipformer-small-en-2023-06-26": {
                "kind": "sherpa_transducer", "dir": "sherpa-onnx-zipformer-small-en-2023-06-26",
                "lang": "en", "tier": "min",
                "label": {"ru": "Английский — малая (zipformer)", "en": "English — small (zipformer)"}},
            "sherpa-onnx-zipformer-en-2023-04-01": {
                "kind": "sherpa_transducer", "dir": "sherpa-onnx-zipformer-en-2023-04-01",
                "lang": "en", "tier": "max",
                "label": {"ru": "Английский — большая (zipformer)", "en": "English — large (zipformer)"}},
        },
    },
    "whisper-cpp": {
        # whisper.cpp via the pywhispercpp binding (TODO #14f). ggml models are
        # single .bin files from the ggerganov/whisper.cpp HF repo; the adapter
        # (whispercpp_engine.py) loads them with pywhispercpp.model.Model. Same
        # multilingual whisper weights, but a CPU-efficient C++ runtime — offered
        # so the user can compare CPU speed/quality against the torch engines.
        "label": {"ru": "whisper.cpp (офлайн, ggml, CPU-эффективный)",
                  "en": "whisper.cpp (offline, ggml, CPU-efficient)"},
        "multilingual": True,          # one model serves every language (ru + en)
        "model_field": "size",         # whisperModel holds a size key
        "default_model": "base",
        "download": "whispercpp_ggml", # single ggml .bin from HuggingFace
        "implemented": True,
        "models": {
            "tiny":   {"kind": "whispercpp_ggml", "file": "ggml-tiny.bin",     "approx_mb": 75},
            "base":   {"kind": "whispercpp_ggml", "file": "ggml-base.bin",     "approx_mb": 142},
            "small":  {"kind": "whispercpp_ggml", "file": "ggml-small.bin",    "approx_mb": 466},
            "medium": {"kind": "whispercpp_ggml", "file": "ggml-medium.bin",   "approx_mb": 1500},
            "large":  {"kind": "whispercpp_ggml", "file": "ggml-large-v3.bin", "approx_mb": 3100},
        },
    },
    "funasr": {
        # FunASR-family models (SenseVoice, Paraformer) run through the ALREADY
        # installed sherpa-onnx runtime (from_sense_voice / from_paraformer) — no
        # heavy `funasr`/modelscope package, no risk to the torch/whisperx stack
        # (owner decision, TODO #14f). EN-focused: these models cover en/zh/ja/ko
        # but have NO Russian — the UI hides them for language=ru. Archives are the
        # same k2-fsa release .tar.bz2 as sherpa-onnx, extracted to funasr_models/.
        "label": {"ru": "FunASR (SenseVoice/Paraformer, EN — ONNX, без RU)",
                  "en": "FunASR (SenseVoice/Paraformer, EN — ONNX, no RU)"},
        "multilingual": False,         # no single model covers ru — gated to en
        "model_field": "name",
        "default_model": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        "download": "funasr_targz",    # k2-fsa release .tar.bz2 -> extract
        "implemented": True,
        "models": {
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17": {
                "kind": "funasr_onnx",
                "dir": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
                "model_type": "sense_voice", "lang": "en", "tier": "min", "approx_mb": 235,
                "label": {"ru": "SenseVoice (EN/ZH/JA/KO/YUE)",
                          "en": "SenseVoice (EN/ZH/JA/KO/YUE)"}},
            "sherpa-onnx-paraformer-en-2024-03-09": {
                "kind": "funasr_onnx",
                "dir": "sherpa-onnx-paraformer-en-2024-03-09",
                "model_type": "paraformer", "lang": "en", "tier": "max", "approx_mb": 220,
                "label": {"ru": "Paraformer (English)", "en": "Paraformer (English)"}},
        },
    },
    "sherpa-extra": {
        # OPTIONAL community models runnable on the ALREADY-installed sherpa-onnx
        # runtime (GigaAM RU via from_nemo_ctc, Moonshine EN via from_moonshine).
        # NOT primary and NOT bundled in ANY build variant (not even full) — they
        # are download-only extras; the UI marks the engine "extra". Same k2-fsa
        # .tar.bz2 source as sherpa/funasr. NOTE some carry a non-commercial licence
        # (baked into the model label).
        "label": {"ru": "Дополнительные модели (доустановка, не в сборке)",
                  "en": "Extra models (download-only, not bundled)"},
        "multilingual": False,         # per-model language
        "model_field": "name",
        "extra": True,                 # excluded from VARIANTS / the distribution
        "default_model": "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19",
        "download": "sherpa_extra_targz",
        "implemented": True,
        "models": {
            "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19": {
                "kind": "sherpa_extra_dir",
                "dir": "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19",
                "model_type": "nemo_ctc", "lang": "ru", "tier": "extra", "approx_mb": 230,
                "label": {"ru": "GigaAM v2 CTC — русский (некоммерч. лицензия)",
                          "en": "GigaAM v2 CTC — Russian (non-commercial licence)"}},
            "sherpa-onnx-nemo-ctc-giga-am-russian-2024-10-24": {
                "kind": "sherpa_extra_dir",
                "dir": "sherpa-onnx-nemo-ctc-giga-am-russian-2024-10-24",
                "model_type": "nemo_ctc", "lang": "ru", "tier": "extra", "approx_mb": 265,
                "label": {"ru": "GigaAM v1 CTC — русский (некоммерч. лицензия)",
                          "en": "GigaAM v1 CTC — Russian (non-commercial licence)"}},
            "sherpa-onnx-moonshine-tiny-en-int8": {
                "kind": "sherpa_extra_dir",
                "dir": "sherpa-onnx-moonshine-tiny-en-int8",
                "model_type": "moonshine", "lang": "en", "tier": "extra", "approx_mb": 120,
                "label": {"ru": "Moonshine tiny — английский (быстрый)",
                          "en": "Moonshine tiny — English (fast)"}},
            "sherpa-onnx-moonshine-base-en-int8": {
                "kind": "sherpa_extra_dir",
                "dir": "sherpa-onnx-moonshine-base-en-int8",
                "model_type": "moonshine", "lang": "en", "tier": "extra", "approx_mb": 240,
                "label": {"ru": "Moonshine base — английский",
                          "en": "Moonshine base — English"}},
        },
    },
}


# --- build-variant PROPOSAL (confirm/adjust with owner) -----------------------
# Principle (owner): a smaller build is NOT reduced in FUNCTIONALITY — every
# variant ships EVERY engine — it only drops the heavy models. whisperx needs no
# entry of its own: it runs on the faster-whisper models, which are in every
# variant. Anything not bundled can still be fetched on demand via download.
# Packaging (TODO #12) reads this to decide which model files to include.
VARIANTS = {
    "minimal": [   # all engines, light models only
        ("whisper", "small"),
        ("faster-whisper", "small"),           # also powers whisperx
        ("whisper-cpp", "base"),
        ("vosk", "vosk-model-small-ru-0.22"),
        ("vosk", "vosk-model-small-en-us-0.15"),
    ],
    "medium": [    # default: add the medium tier, still every engine
        ("whisper", "small"), ("whisper", "medium"),
        ("faster-whisper", "small"), ("faster-whisper", "medium"),
        ("whisper-cpp", "base"), ("whisper-cpp", "small"),
        ("vosk", "vosk-model-small-ru-0.22"),
        ("vosk", "vosk-model-small-en-us-0.15"),
    ],
    "full": [      # every engine, ONE medium-tier model each, both languages (owner spec)
        ("whisper", "medium"),                 # multilingual (ru+en); also powers whisperx via faster
        ("faster-whisper", "medium"),          # multilingual; whisperx rides this
        ("whisper-cpp", "medium"),             # multilingual ggml (golden mean; not tiny/base)
        # vosk is per-language and was hard to source — keep all four (owner: leave as-is)
        ("vosk", "vosk-model-ru-0.22"), ("vosk", "vosk-model-small-ru-0.22"),
        ("vosk", "vosk-model-en-us-0.22"), ("vosk", "vosk-model-small-en-us-0.15"),
        # sherpa-onnx is per-language: one RU + one EN
        ("sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18"),
        ("sherpa-onnx", "sherpa-onnx-zipformer-small-en-2023-06-26"),
        # funasr is EN-only (no RU exists) — the default SenseVoice covers en/zh/ja/ko/yue
        ("funasr", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"),
    ],
}


# --- resolution & availability (pure stdlib) ----------------------------------
def resources_dir(override: Optional[str] = None) -> str:
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))   # backend/
    return os.path.normpath(os.path.join(here, "..", "resources"))


def _engine(engine: str) -> Optional[dict]:
    """Return the engine spec, transparently resolving ``uses`` (whisperx ->
    faster-whisper) so whisperx shares faster-whisper's model table."""
    e = ENGINES.get(engine)
    if e is None:
        return None
    if e.get("uses"):
        base = ENGINES[e["uses"]]
        merged = dict(e)
        merged["models"] = base["models"]
        return merged
    return e


def list_models(engine: str, language: Optional[str] = None) -> list:
    """Model ids selectable for ``engine``. For language-specific engines
    (vosk) the list is filtered by ``language`` when given."""
    e = _engine(engine)
    if not e or not e.get("models"):
        return []
    out = []
    for mid, m in e["models"].items():
        if language and not e.get("multilingual", True) and m.get("lang") != language:
            continue
        out.append(mid)
    return out


def is_implemented(engine: str) -> bool:
    """True if ``engine`` has a working transcribe adapter. The UI MUST NOT
    offer download/selection for an engine that is declared but not implemented
    (otherwise a user could fetch a model and be unable to use it — see TODO #14)."""
    return bool((ENGINES.get(engine) or {}).get("implemented", False))


def default_model(engine: str) -> Optional[str]:
    e = ENGINES.get(engine) or {}
    return e.get("default_model")


def _is_vosk_model_dir(path: str) -> bool:
    # A usable Vosk model directory always contains a 'conf' subfolder.
    return os.path.isdir(path) and os.path.isdir(os.path.join(path, "conf"))


def _is_sherpa_model_dir(path: str) -> bool:
    # A usable sherpa transducer model dir has tokens.txt + encoder/decoder/joiner.
    if not os.path.isdir(path):
        return False
    has = lambda pat: bool(glob.glob(os.path.join(path, pat)))
    return (os.path.isfile(os.path.join(path, "tokens.txt"))
            and has("encoder*.onnx") and has("decoder*.onnx") and has("joiner*.onnx"))


def _is_funasr_model_dir(path: str) -> bool:
    # A usable SenseVoice/Paraformer dir has tokens.txt + a single model[.int8].onnx.
    if not os.path.isdir(path):
        return False
    return (os.path.isfile(os.path.join(path, "tokens.txt"))
            and bool(glob.glob(os.path.join(path, "model*.onnx"))))


def _is_extra_model_dir(path: str, model_type: str) -> bool:
    # Validity depends on the architecture: NeMo-CTC (GigaAM) is a single model
    # onnx; Moonshine is a 4-file bundle (preprocess/encode/uncached/cached).
    if not os.path.isdir(path):
        return False
    if not os.path.isfile(os.path.join(path, "tokens.txt")):
        return False
    has = lambda pat: bool(glob.glob(os.path.join(path, pat)))
    if model_type == "moonshine":
        return (has("preprocess*.onnx") and has("encode*.onnx")
                and has("uncached_decode*.onnx") and has("cached_decode*.onnx"))
    return has("model*.onnx")   # nemo_ctc and other single-onnx architectures


def resolve_model_path(engine: str, model: str, language: Optional[str] = None,
                       res_dir: Optional[str] = None) -> Optional[str]:
    """Absolute path to the model on disk, or None if it is not present."""
    res = resources_dir(res_dir)
    e = _engine(engine)
    if not e or not e.get("models"):
        return None
    m = e["models"].get(model)
    if not m:
        return None
    kind = m["kind"]
    if kind == "whisper_pt":
        p = os.path.join(res, "whisper_models", m["file"])
        return p if os.path.isfile(p) else None
    if kind == "faster_hf":
        p = os.path.join(res, "whisper_models", m["dir"])
        return p if (os.path.isdir(p) and os.listdir(p)) else None
    if kind == "vosk_dir":
        for d in _VOSK_DIRS:
            p = os.path.join(res, d, m["dir"])
            if _is_vosk_model_dir(p):
                return p
        return None
    if kind == "sherpa_transducer":
        p = os.path.join(res, _SHERPA_DIR, m["dir"])
        return p if _is_sherpa_model_dir(p) else None
    if kind == "whispercpp_ggml":
        p = os.path.join(res, _WHISPERCPP_DIR, m["file"])
        return p if os.path.isfile(p) else None
    if kind == "funasr_onnx":
        p = os.path.join(res, _FUNASR_DIR, m["dir"])
        return p if _is_funasr_model_dir(p) else None
    if kind == "sherpa_extra_dir":
        p = os.path.join(res, _EXTRA_DIR, m["dir"])
        return p if _is_extra_model_dir(p, m.get("model_type", "")) else None
    return None


def is_available(engine: str, model: str, language: Optional[str] = None,
                 res_dir: Optional[str] = None) -> bool:
    return resolve_model_path(engine, model, language, res_dir) is not None


def intended_path(engine: str, model: str,
                  res_dir: Optional[str] = None) -> Optional[str]:
    """Where a model SHOULD live on disk (whether or not it is present yet).
    The download flow uses this as its target; ``resolve_model_path`` is the
    same path gated by an existence/validity check."""
    res = resources_dir(res_dir)
    e = _engine(engine)
    if not e or not e.get("models"):
        return None
    m = e["models"].get(model)
    if not m:
        return None
    kind = m["kind"]
    if kind == "whisper_pt":
        return os.path.join(res, "whisper_models", m["file"])
    if kind == "faster_hf":
        return os.path.join(res, "whisper_models", m["dir"])
    if kind == "vosk_dir":
        return os.path.join(res, _VOSK_DIRS[0], m["dir"])
    if kind == "sherpa_transducer":
        return os.path.join(res, _SHERPA_DIR, m["dir"])
    if kind == "whispercpp_ggml":
        return os.path.join(res, _WHISPERCPP_DIR, m["file"])
    if kind == "funasr_onnx":
        return os.path.join(res, _FUNASR_DIR, m["dir"])
    if kind == "sherpa_extra_dir":
        return os.path.join(res, _EXTRA_DIR, m["dir"])
    return None


def vosk_download_url(model: str) -> str:
    """Official alphacephei archive URL for a Vosk model name."""
    return f"https://alphacephei.com/vosk/models/{model}.zip"


def sherpa_download_url(model: str) -> str:
    """k2-fsa release archive URL for a sherpa-onnx model name."""
    return f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{model}.tar.bz2"


def whispercpp_download_url(size: str) -> str:
    """HuggingFace ggerganov/whisper.cpp URL for a ggml model of the given size."""
    fname = ENGINES["whisper-cpp"]["models"][size]["file"]
    return f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{fname}"
