"""Interactive dependency installer for the *min* distribution.

The min archive ships source only: no embedded runtime, no models. This script
scans the machine, recommends what fits it, lets the user pick, and then installs
exactly that. It must run on a **bare system Python** before anything is
installed, so it uses the standard library ONLY.

    python desktop\\packaging\\installer.py            # interactive
    python desktop\\packaging\\installer.py --recommended --yes
    python desktop\\packaging\\installer.py --plan-only  # print the plan, install nothing

Version pins are never duplicated here: they are parsed out of
``desktop/requirements.txt`` and ``server/requirements.txt``, so the installer
cannot drift away from the manifests. A package named in a group but absent from
the manifests is a hard error (``_selftest_installer.py`` asserts this).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESKTOP_REQ = ROOT / "desktop" / "requirements.txt"
BACKEND_REQ = ROOT / "backend" / "requirements.txt"
SERVER_REQ = ROOT / "server" / "requirements.txt"

MIN_PYTHON = (3, 9)
# Upper bound, and it is NOT cosmetic: the pinned stack tops out at cp312.
# `numpy<2.0` (backend/requirements.txt) publishes wheels for cp39-cp312 only, so
# on 3.13 pip falls back to building numpy from source and dies with
# "metadata-generation-failed" a minute into the install - after the user has
# already agreed to the plan. Raising this bound means bumping numpy to 2.x and
# re-validating torch/whisperx/ctranslate2 together, which is a version upgrade,
# not an installer tweak. Until then, refuse early and say where to get a
# supported Python.
MAX_PYTHON = (3, 12)
PINNED_PYTHON = "3.11"

TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# ---------------------------------------------------------------------------
# Text (RU/EN). Keys are shared; the installer picks one dict at startup.
# ---------------------------------------------------------------------------
T = {
    "ru": {
        "title": "Meeting Summarizer - установка зависимостей",
        "scanning": "Проверяю машину...",
        "machine": "Машина",
        "python": "Python",
        "cpu": "Процессор",
        "ram": "Оперативная память",
        "disk": "Свободно на диске",
        "gpu": "Видеокарта",
        "no_gpu": "NVIDIA-видеокарта не найдена",
        "cores": "ядер",
        "recommendation": "Рекомендация",
        "torch_cuda": "Ставим torch со сборкой под CUDA 12.4 - транскрибация пойдёт на видеокарте.",
        "torch_cpu": "Ставим torch для CPU: подходящей NVIDIA-видеокарты нет. Всё будет работать, но медленнее.",
        "components": "Что установить",
        "required": "обязательно",
        "recommended_mark": "рекомендуется",
        "engines_header": "Движки транскрибации (можно выбрать несколько)",
        "torch_header": "Сборка PyTorch (её используют Whisper, Faster-Whisper и WhisperX)",
        "torch_pick_cuda": "CUDA — использовать видеокарту (быстрее в разы, ~2.5 ГБ)",
        "torch_pick_cpu": "Только CPU — без видеокарты (медленнее, ~200 МБ)",
        "torch_no_gpu": "видеокарта NVIDIA не обнаружена — CUDA-сборка работать не будет",
        "torch_choice": "Ваш выбор [1/2]",
        "models_header": "Модели для скачивания",
        "llm_header": "Встроенная локальная ИИ-модель",
        "llm_ok": "Машина потянет локальную модель. Рекомендуется: {model} (~{size} ГБ, нужно {vram} ГБ VRAM).",
        "llm_cpu_only": "Видеокарты нет, но {ram} ГБ ОЗУ хватит для маленькой модели на процессоре. Будет медленно: считайте минуты на одно саммари.",
        "llm_no": "Эта машина не подходит для локальной ИИ-модели: {reason}. Используйте облачного провайдера (OpenAI/Anthropic/Google/xAI) или локальный агент-CLI - настраивается в Настройках, ничего скачивать не нужно.",
        "llm_reason_vram": "мало VRAM ({vram} ГБ) и мало ОЗУ ({ram} ГБ)",
        "prompt_toggle": "Номер - переключить, [Enter] - принять, a - всё, n - ничего, q - выход",
        "your_choice": "Выбор",
        "plan": "План установки",
        "download_size": "Скачать примерно",
        "disk_needed": "Займёт на диске примерно",
        "not_enough_disk": "ВНИМАНИЕ: на диске свободно {free} ГБ, а нужно около {need} ГБ.",
        "confirm": "Начать установку? [y/N]",
        "aborted": "Отменено. Ничего не установлено.",
        "installing": "Устанавливаю",
        "step": "Шаг",
        "done": "Готово.",
        "done_next": "Дальше: RUN.bat - десктоп-приложение, SERVER.bat - веб-кабинет.",
        "failed": "ОШИБКА на шаге",
        "failed_hint": "Установка остановлена. Исправьте причину выше и запустите INSTALL.bat снова - уже установленное переустанавливаться не будет.",
        "py_too_old": "Нужен Python {need} или новее, а установлен {have}. Скачайте {pinned} с python.org и запустите снова.",
        "py_too_new": "Установлен Python {have}, а поддерживается до {maxv} включительно.\n"
                      "Причина: numpy<2.0 (его требуют текущие версии torch и движков) не собирается\n"
                      "под {have} — колёс нет, и pip попытается компилировать его из исходников.\n"
                      "Что делать: поставьте Python {pinned} с python.org (можно рядом с текущим) и\n"
                      "запустите INSTALL.bat снова. Проверить установленные версии: py -0p",
        "py_not_64": "Нужен 64-битный Python: torch и движки не собираются под 32 бита.",
        "no_pip": "В этом Python нет pip. Выполните: python -m ensurepip --upgrade",
        "models_note": "Модели качаются после установки пакетов - иначе загрузчику нечем работать.",
        "nothing_selected": "Не выбран ни один движок - без них транскрибация невозможна.",
        "lang_prompt": "Language / Язык: [1] Русский  [2] English",
    },
    "en": {
        "title": "Meeting Summarizer - dependency installer",
        "scanning": "Scanning this machine...",
        "machine": "Machine",
        "python": "Python",
        "cpu": "CPU",
        "ram": "RAM",
        "disk": "Free disk space",
        "gpu": "GPU",
        "no_gpu": "no NVIDIA GPU found",
        "cores": "cores",
        "recommendation": "Recommendation",
        "torch_cuda": "Installing the CUDA 12.4 torch build - transcription will run on the GPU.",
        "torch_cpu": "Installing the CPU torch build: no suitable NVIDIA GPU found. Everything still works, just slower.",
        "components": "What to install",
        "required": "required",
        "recommended_mark": "recommended",
        "engines_header": "Transcription engines (pick any number)",
        "torch_header": "PyTorch build (used by Whisper, Faster-Whisper and WhisperX)",
        "torch_pick_cuda": "CUDA - use the graphics card (several times faster, ~2.5 GB)",
        "torch_pick_cpu": "CPU only - no graphics card (slower, ~200 MB)",
        "torch_no_gpu": "no NVIDIA card detected - a CUDA build would not run",
        "torch_choice": "Your choice [1/2]",
        "models_header": "Models to download",
        "llm_header": "Built-in local AI model",
        "llm_ok": "This machine can run a local model. Recommended: {model} (~{size} GB, needs {vram} GB VRAM).",
        "llm_cpu_only": "No GPU, but {ram} GB of RAM is enough for a small model on the CPU. Expect minutes per summary.",
        "llm_no": "This machine is not suitable for a local AI model: {reason}. Use a cloud provider (OpenAI/Anthropic/Google/xAI) or a local agent CLI - configured in Settings, nothing to download.",
        "llm_reason_vram": "not enough VRAM ({vram} GB) and not enough RAM ({ram} GB)",
        "prompt_toggle": "number - toggle, [Enter] - accept, a - all, n - none, q - quit",
        "your_choice": "Choice",
        "plan": "Installation plan",
        "download_size": "Approximate download",
        "disk_needed": "Approximate disk use",
        "not_enough_disk": "WARNING: {free} GB free, about {need} GB needed.",
        "confirm": "Start the installation? [y/N]",
        "aborted": "Aborted. Nothing was installed.",
        "installing": "Installing",
        "step": "Step",
        "done": "Done.",
        "done_next": "Next: RUN.bat for the desktop app, SERVER.bat for the web cabinet.",
        "failed": "FAILED at step",
        "failed_hint": "Installation stopped. Fix the cause above and run INSTALL.bat again - what is already installed will not be reinstalled.",
        "py_too_old": "Python {need} or newer is required, found {have}. Install {pinned} from python.org and try again.",
        "py_too_new": "Found Python {have}, but {maxv} is the highest supported.\n"
                      "Why: numpy<2.0 - required by the pinned torch and engine versions - has no\n"
                      "wheels for {have}, so pip would try to compile it from source and fail.\n"
                      "Fix: install Python {pinned} from python.org (it can sit next to the one you\n"
                      "have) and run INSTALL.bat again. List installed versions with: py -0p",
        "py_not_64": "A 64-bit Python is required: torch and the engines have no 32-bit builds.",
        "no_pip": "This Python has no pip. Run: python -m ensurepip --upgrade",
        "models_note": "Models are fetched after the packages - the downloader needs them.",
        "nothing_selected": "No engine selected - transcription is impossible without one.",
        "lang_prompt": "Language / Язык: [1] Русский  [2] English",
    },
}

# ---------------------------------------------------------------------------
# Component groups. Package names only - the pinned spec comes from the manifests.
# ---------------------------------------------------------------------------
CORE_PACKAGES = ["PySide6", "numpy", "requests", "huggingface-hub", "yt-dlp",
                 "reportlab", "python-docx", "psutil"]
TORCH_PACKAGES = ["torch", "torchaudio", "torchvision"]
RAG_PACKAGES = ["chromadb", "pydantic", "tenacity", "python-dotenv",
                "sentence-transformers"]
PYANNOTE_PACKAGES = ["pyannote.audio"]

# engine id (from backend/engines_registry.py) -> pip packages it needs
ENGINE_PACKAGES = {
    "whisper": ["openai-whisper", "scipy"],
    "faster-whisper": ["faster-whisper", "ctranslate2"],
    "whisperx": ["whisperx", "faster-whisper", "ctranslate2"],
    "vosk": ["vosk"],
    "sherpa-onnx": ["sherpa-onnx", "onnxruntime"],
    "whisper-cpp": ["pywhispercpp"],
    "funasr": ["sherpa-onnx", "onnxruntime"],
}
# Engines whose runtime is the torch stack. The rest run without torch at all.
TORCH_ENGINES = {"whisper", "faster-whisper", "whisperx"}

ENGINE_HINT = {
    "ru": {
        "whisper": "эталонное качество, самый медленный",
        "faster-whisper": "в 2-4 раза быстрее, то же качество - лучший выбор по умолчанию",
        "whisperx": "самый быстрый и единственный, кто размечает спикеров",
        "vosk": "лёгкий, работает офлайн на слабой машине, качество ниже",
        "sherpa-onnx": "офлайн через onnxruntime, без torch",
        "whisper-cpp": "экономичен по процессору, без torch",
        "funasr": "только английский/китайский/японский/корейский, РУССКОГО НЕТ",
    },
    "en": {
        "whisper": "reference quality, slowest",
        "faster-whisper": "2-4x faster at the same quality - the sensible default",
        "whisperx": "fastest, and the only engine that labels speakers",
        "vosk": "lightweight, offline, runs on weak hardware, lower quality",
        "sherpa-onnx": "offline via onnxruntime, no torch needed",
        "whisper-cpp": "CPU-efficient, no torch needed",
        "funasr": "English/Chinese/Japanese/Korean only - NO RUSSIAN",
    },
}

# Rough installed-size estimates (MB) for the disk-space warning.
SIZE_MB = {"core": 350, "torch_cuda": 2800, "torch_cpu": 350, "rag": 900,
           "server": 60, "pyannote": 250}
ENGINE_SIZE_MB = {"whisper": 30, "faster-whisper": 60, "whisperx": 120,
                  "vosk": 15, "sherpa-onnx": 90, "whisper-cpp": 25, "funasr": 0}


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------
def parse_requirements(path: Path) -> dict:
    """{normalised package name: full requirement line} from a requirements file.

    Commented-out lines are ignored on purpose: they mark optional extras that
    the user opts into explicitly, and they must not be installed by default.
    """
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[;]", line)[0].strip()
        if name:
            out[name.lower().replace("_", "-")] = line
    return out


def desktop_pins() -> dict:
    """Pins for everything the desktop side can install.

    ``backend/requirements.txt`` first, then ``desktop/requirements.txt`` on top:
    the desktop manifest carries the exact verified pins, the backend one fills
    in packages the desktop manifest lists only as opt-in extras (pyannote).
    """
    pins = parse_requirements(BACKEND_REQ)
    pins.update(parse_requirements(DESKTOP_REQ))
    return pins


def resolve(packages, pins: dict) -> list:
    """Pinned requirement lines for ``packages``. Unknown package = drift = error."""
    missing = [p for p in packages if p.lower().replace("_", "-") not in pins]
    if missing:
        raise KeyError("not declared in the requirements manifests: "
                       + ", ".join(sorted(missing)))
    seen, out = set(), []
    for p in packages:
        spec = pins[p.lower().replace("_", "-")]
        if spec not in seen:
            seen.add(spec)
            out.append(spec)
    return out


# ---------------------------------------------------------------------------
# Machine probe
# ---------------------------------------------------------------------------
class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _ram_gb() -> float:
    try:
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys / (1024 ** 3), 1)
    except Exception:  # noqa: BLE001 - any probe failure just means "unknown"
        pass
    return 0.0


def _nvidia() -> tuple:
    """(gpu name, VRAM GB, driver version) or ("", 0.0, "")."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not (out.stdout or "").strip():
            return "", 0.0, ""
        name, mem, driver = [p.strip() for p in
                             out.stdout.strip().splitlines()[0].split(",")[:3]]
        return name, round(int(mem) / 1024.0, 1), driver
    except Exception:  # noqa: BLE001
        return "", 0.0, ""


def probe() -> dict:
    gpu, vram, driver = _nvidia()
    try:
        free_gb = round(shutil.disk_usage(str(ROOT)).free / (1024 ** 3), 1)
    except OSError:
        free_gb = 0.0
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "python_tuple": sys.version_info[:2],
        "is_64bit": sys.maxsize > 2 ** 32,
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": _ram_gb(),
        "free_disk_gb": free_gb,
        "gpu": gpu,
        "vram_gb": vram,
        "driver": driver,
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------
def recommend(p: dict) -> dict:
    """What this machine should install. Pure function of the probe - unit-tested."""
    cuda = bool(p["gpu"]) and p["vram_gb"] >= 4.0
    if cuda:
        engines = ["faster-whisper", "whisperx"]
    elif p["ram_gb"] >= 8:
        engines = ["faster-whisper", "whisper-cpp"]
    else:
        engines = ["vosk", "whisper-cpp"]

    # Local LLM: VRAM is what makes it usable; RAM-only is a slow fallback.
    if p["vram_gb"] >= 4.0:
        llm = "gpu"
    elif p["ram_gb"] >= 16.0:
        llm = "cpu"
    else:
        llm = "no"

    return {
        "torch": "cuda" if cuda else "cpu",
        "engines": engines,
        "rag": p["ram_gb"] >= 8.0,
        "server": False,
        "pyannote": False,
        "local_llm": llm,
    }


def local_llm_message(p: dict, rec: dict, t: dict) -> str:
    if rec["local_llm"] == "no":
        reason = t["llm_reason_vram"].format(vram=p["vram_gb"], ram=p["ram_gb"])
        return t["llm_no"].format(reason=reason)
    if rec["local_llm"] == "cpu":
        return t["llm_cpu_only"].format(ram=p["ram_gb"])
    model, size, vram = _llm_pick(p["vram_gb"])
    return t["llm_ok"].format(model=model, size=size, vram=vram)


def _llm_pick(vram_gb: float) -> tuple:
    """Largest curated GGUF that fits this VRAM: (id, size GB, needed VRAM GB).

    Reuses backend/local_ai.py's catalog when importable (min ships it), so the
    installer never carries a second copy of the model list.
    """
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import local_ai  # noqa: PLC0415 - optional, resolved at call time
        catalog = local_ai.CATALOG
    except Exception:  # noqa: BLE001
        return "qwen3-4b", 2.3, 4
    best = None
    for key, info in sorted(catalog.items(),
                            key=lambda kv: kv[1]["vram_gb"], reverse=True):
        if vram_gb >= info["vram_gb"]:
            best = (key, info["size_gb"], info["vram_gb"])
            break
    if best is None:
        smallest = min(catalog.items(), key=lambda kv: kv[1]["vram_gb"])
        best = (smallest[0], smallest[1]["size_gb"], smallest[1]["vram_gb"])
    return best


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def build_plan(selection: dict, pins: dict, server_pins: dict) -> dict:
    """Turn a selection into ordered pip steps + model downloads + size estimates."""
    steps = []
    torch_index = (TORCH_CUDA_INDEX if selection["torch"] == "cuda"
                   else TORCH_CPU_INDEX)
    needs_torch = bool(set(selection["engines"]) & TORCH_ENGINES)

    steps.append({"label": "core", "args": resolve(CORE_PACKAGES, pins)})
    if needs_torch:
        torch_args = resolve(TORCH_PACKAGES, pins)
        if selection["torch"] != "cuda":
            # The manifest pins the CUDA wheels (torch==2.6.0+cu124). That local
            # version does not exist on the CPU index, so a CPU install would die
            # with "no matching distribution" - drop the +cuXXX suffix.
            torch_args = [re.sub(r"\+cu\d+", "", a) for a in torch_args]
        steps.append({"label": "torch", "args": torch_args,
                      "index_url": torch_index})

    engine_packages = []
    for eng in selection["engines"]:
        engine_packages += ENGINE_PACKAGES[eng]
    if engine_packages:
        steps.append({"label": "engines", "args": resolve(engine_packages, pins)})
    if selection["rag"]:
        steps.append({"label": "rag", "args": resolve(RAG_PACKAGES, pins)})
    if selection["pyannote"]:
        steps.append({"label": "pyannote",
                      "args": resolve(PYANNOTE_PACKAGES, pins)})
    if selection["server"]:
        steps.append({"label": "server",
                      "args": resolve(list(server_pins), server_pins)})

    size = SIZE_MB["core"]
    if needs_torch:
        size += SIZE_MB["torch_cuda" if selection["torch"] == "cuda" else "torch_cpu"]
    size += sum(ENGINE_SIZE_MB.get(e, 0) for e in selection["engines"])
    if selection["rag"]:
        size += SIZE_MB["rag"]
    if selection["pyannote"]:
        size += SIZE_MB["pyannote"]
    if selection["server"]:
        size += SIZE_MB["server"]

    models = list(selection.get("models", []))
    size += sum(mb for _, _, mb in models)
    llm = selection.get("local_llm_model") or ""
    if llm:
        size += int(_llm_size_gb(llm) * 1024)
    return {"steps": steps, "models": models, "local_llm_model": llm,
            "size_mb": size, "torch_index": torch_index}


def _llm_size_gb(model_id: str) -> float:
    """Download size of a curated GGUF, from local_ai's catalog (0.0 if unknown)."""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import local_ai  # noqa: PLC0415
        return float(local_ai.CATALOG[model_id]["size_gb"])
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# Model catalogue for the picker
# ---------------------------------------------------------------------------
def models_for(engines: list) -> list:
    """[(engine, model, approx MB)] offered for the chosen engines, small first."""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import engines_registry as reg  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out = []
    for eng in engines:
        spec = reg.ENGINES.get(eng) or {}
        # whisperx has no models of its own - it runs faster-whisper's.
        source = "faster-whisper" if eng == "whisperx" else eng
        models = (reg.ENGINES.get(source) or {}).get("models") or {}
        for name, info in models.items():
            out.append((source, name, int(info.get("approx_mb") or 0)))
    seen, uniq = set(), []
    for item in out:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            uniq.append(item)
    return sorted(uniq, key=lambda x: x[2])


def hardware_tier(p: dict) -> str:
    """Which of the registry's curated model sets this machine should get."""
    if p["vram_gb"] >= 8.0:
        return "full"
    if p["vram_gb"] >= 4.0 or p["ram_gb"] >= 16.0:
        return "medium"
    return "minimal"


def default_models(engines: list, tier: str = "medium") -> list:
    """Preselected models: the registry's own curated set for this hardware tier,
    narrowed to the chosen engines. Engines the set does not cover fall back to
    their smallest model, so every chosen engine is usable after the install."""
    available = models_for(engines)
    by_engine = {}
    for eng, model, mb in available:
        by_engine.setdefault(eng, []).append((eng, model, mb))

    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import engines_registry as reg  # noqa: PLC0415
        curated = reg.VARIANTS.get(tier, [])
    except Exception:  # noqa: BLE001
        curated = []

    picked, covered = [], set()
    lookup = {(e, m): (e, m, mb) for e, m, mb in available}
    for eng, model in curated:
        if (eng, model) in lookup:
            picked.append(lookup[(eng, model)])
            covered.add(eng)
    for eng, items in by_engine.items():
        if eng not in covered:
            usable = [i for i in items if i[2] >= 100] or items
            picked.append(usable[0])
    return picked


# ---------------------------------------------------------------------------
# Console UI
# ---------------------------------------------------------------------------
def setup_console() -> None:
    """Make this console able to print Russian.

    A stock ``cmd.exe`` runs on a legacy code page (866/1251), so the RU strings
    below would either mojibake or raise UnicodeEncodeError - and this script is
    the FIRST thing a recipient runs, before anything is installed. Switch the
    console to UTF-8 and reconfigure the streams to match.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 - not a Windows console; streams still help
        pass
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - redirected/not reconfigurable
            pass


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def pick_language() -> str:
    if os.environ.get("MS_INSTALLER_LANG") in ("ru", "en"):
        return os.environ["MS_INSTALLER_LANG"]
    print(T["ru"]["lang_prompt"])
    return "en" if _ask("> ") == "2" else "ru"


def _menu(title: str, options: list, selected: set, t: dict) -> set:
    """options: [(key, label, hint)]. Returns the chosen key set."""
    while True:
        print(f"\n{title}")
        for i, (key, label, hint) in enumerate(options, 1):
            mark = "[x]" if key in selected else "[ ]"
            print(f"  {i:2d}. {mark} {label}")
            if hint:
                print(f"          {hint}")
        print(f"  ({t['prompt_toggle']})")
        raw = _ask(f"{t['your_choice']}> ").lower()
        if raw in ("", "ok"):
            return selected
        if raw == "q":
            sys.exit(1)
        if raw == "a":
            selected = {k for k, _, _ in options}
            continue
        if raw == "n":
            selected = set()
            continue
        for token in re.split(r"[,\s]+", raw):
            if token.isdigit() and 1 <= int(token) <= len(options):
                key = options[int(token) - 1][0]
                selected.symmetric_difference_update({key})


def _fmt_mb(mb: int) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


def interactive(p_probe: dict, rec: dict, t: dict, lang: str) -> dict:
    engine_opts = [(e, e, ENGINE_HINT[lang].get(e, "")) for e in ENGINE_PACKAGES]
    engines = _menu(t["engines_header"], engine_opts, set(rec["engines"]), t)
    while not engines:
        print(f"  ! {t['nothing_selected']}")
        engines = _menu(t["engines_header"], engine_opts, set(rec["engines"]), t)

    # CUDA or CPU is the single biggest download decision, and it used to be made
    # FOR the user from the probe alone: someone with a broken driver, a shared
    # machine or simply no wish to pull 2.5 GB had no say.
    torch_choice = rec["torch"]
    if set(engines) & TORCH_ENGINES:
        recommended = rec["torch"]
        print()
        print(t['torch_header'])
        cuda_note = "" if p_probe.get("gpu") else f"  ({t['torch_no_gpu']})"
        star = " *" if recommended == "cuda" else ""
        print(f"   1. {t['torch_pick_cuda']}{star}{cuda_note}")
        print(f"   2. {t['torch_pick_cpu']}{' *' if recommended == 'cpu' else ''}")
        answer = _ask(f"  {t['torch_choice']}> ").strip()
        if answer == "1":
            torch_choice = "cuda"
        elif answer == "2":
            torch_choice = "cpu"

    extra_opts = [
        ("rag", "RAG / knowledge base",
         "chromadb + локальные эмбеддинги, ~900 MB" if lang == "ru"
         else "chromadb + local embeddings, ~900 MB"),
        ("server", "Web cabinet (SERVER.bat)",
         "многопользовательский веб-интерфейс, ~60 MB" if lang == "ru"
         else "multi-user browser cabinet, ~60 MB"),
        ("pyannote", "pyannote diarization",
         "точнее спикеры, но нужен токен Hugging Face; по умолчанию работает "
         "офлайн-sherpa без токена" if lang == "ru"
         else "better speakers, but needs a Hugging Face token; the default "
              "offline sherpa backend needs none"),
    ]
    preset = {k for k in ("rag", "server", "pyannote") if rec[k]}
    extras = _menu(t["components"], extra_opts, preset, t)

    model_opts = [(f"{e}/{m}", f"{e} / {m}", _fmt_mb(mb))
                  for e, m, mb in models_for(sorted(engines))]
    chosen_models = []
    if model_opts:
        preset_models = {f"{e}/{m}" for e, m, _
                         in default_models(sorted(engines), hardware_tier(p_probe))}
        print(f"\n  {t['models_note']}")
        keys = _menu(t["models_header"], model_opts, preset_models, t)
        lookup = {f"{e}/{m}": (e, m, mb) for e, m, mb in models_for(sorted(engines))}
        chosen_models = [lookup[k] for k in keys if k in lookup]

    print(f"\n{t['llm_header']}")
    print(f"  {local_llm_message(p_probe, rec, t)}")
    llm_model = ""
    if rec["local_llm"] != "no":
        model, size, _ = _llm_pick(p_probe["vram_gb"])
        q = (f"  Скачать {model} (~{size} ГБ) сейчас? [y/N] " if lang == "ru"
             else f"  Download {model} (~{size} GB) now? [y/N] ")
        if _ask(q).lower() == "y":
            llm_model = model

    return {"torch": torch_choice, "engines": sorted(engines),
            "rag": "rag" in extras, "server": "server" in extras,
            "pyannote": "pyannote" in extras,
            "models": chosen_models, "local_llm_model": llm_model}


def print_machine(p: dict, t: dict) -> None:
    print(f"\n{t['machine']}:")
    print(f"  {t['python']:22s} {p['python']} ({'64-bit' if p['is_64bit'] else '32-bit'})")
    print(f"  {t['cpu']:22s} {p['cpu_cores']} {t['cores']}")
    print(f"  {t['ram']:22s} {p['ram_gb']} GB")
    print(f"  {t['disk']:22s} {p['free_disk_gb']} GB")
    if p["gpu"]:
        print(f"  {t['gpu']:22s} {p['gpu']}, {p['vram_gb']} GB VRAM, driver {p['driver']}")
    else:
        print(f"  {t['gpu']:22s} {t['no_gpu']}")


def print_plan(plan: dict, p: dict, t: dict) -> None:
    print(f"\n{t['plan']}:")
    for i, step in enumerate(plan["steps"], 1):
        idx = f"  (--index-url {step['index_url']})" if step.get("index_url") else ""
        print(f"  {i}. pip install {' '.join(step['args'])}{idx}")
    for eng, model, mb in plan["models"]:
        print(f"  -> model {eng}/{model} ({_fmt_mb(mb)})")
    if plan["local_llm_model"]:
        print(f"  -> local LLM {plan['local_llm_model']}")
    print(f"\n  {t['disk_needed']}: {_fmt_mb(plan['size_mb'])}")
    need_gb = plan["size_mb"] / 1024
    if p["free_disk_gb"] and p["free_disk_gb"] < need_gb:
        print(f"  {t['not_enough_disk'].format(free=p['free_disk_gb'], need=round(need_gb, 1))}")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_plan(plan: dict, t: dict) -> int:
    total = len(plan["steps"]) + len(plan["models"]) + (1 if plan["local_llm_model"] else 0)
    n = 0
    for step in plan["steps"]:
        n += 1
        cmd = [sys.executable, "-m", "pip", "install", *step["args"]]
        if step.get("index_url"):
            cmd += ["--index-url", step["index_url"]]
        print(f"\n[{t['step']} {n}/{total}] {t['installing']}: {step['label']}")
        if subprocess.run(cmd).returncode != 0:
            print(f"\n{t['failed']} {n}/{total}: {step['label']}")
            print(t["failed_hint"])
            return 1

    for eng, model, _ in plan["models"]:
        n += 1
        print(f"\n[{t['step']} {n}/{total}] {t['installing']}: {eng}/{model}")
        cmd = [sys.executable, str(ROOT / "backend" / "models_cli.py"),
               "download", "--engine", eng, "--model", model]
        if subprocess.run(cmd).returncode != 0:
            print(f"\n{t['failed']} {n}/{total}: {eng}/{model}")
            print(t["failed_hint"])
            return 1

    if plan["local_llm_model"]:
        n += 1
        print(f"\n[{t['step']} {n}/{total}] {t['installing']}: "
              f"local AI ({plan['local_llm_model']})")
        cmd = [sys.executable, str(ROOT / "backend" / "local_ai.py"),
               "install", "--model", plan["local_llm_model"]]
        if subprocess.run(cmd).returncode != 0:
            print(f"\n{t['failed']} {n}/{total}: local AI")
            print(t["failed_hint"])
            return 1

    record_interpreter()
    adopt_installed_model(plan)
    print(f"\n{t['done']}\n{t['done_next']}")
    return 0


def adopt_installed_model(plan: dict) -> bool:
    """Point the app at the engine/model this install actually downloaded.

    The defaults say faster-whisper/medium. On a modest machine the installer
    deliberately fetches something smaller (small, or vosk on a weak box), and
    the app then greeted the recipient on FIRST launch with "the selected model
    medium is not downloaded" - a warning produced by the installer's own choice.

    Only fills in a fresh install: an existing settings.json is left alone, so a
    re-run never overwrites what the user has since chosen.
    """
    models = plan.get("models") or []
    if not models:
        return False
    engine, model = models[0][0], models[0][1]
    cfg = ROOT / "config" / "settings.json"
    if cfg.exists():
        return False
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"transcriptionEngine": engine, "whisperModel": model},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def record_interpreter() -> Path:
    """Remember WHICH python this install went into, for RUN.bat / SERVER.bat.

    A machine can have several. On a clean Windows 11 the first `python` on PATH
    was 3.13 - which this installer refuses - while the install correctly went
    into 3.11; the launchers then started 3.13 and failed with "No module named
    PySide6" on a perfectly good installation. The launchers read this file and
    fall back to plain `python` when it is absent (e.g. the full build, which
    carries its own interpreter).
    """
    target = ROOT / "config" / "interpreter.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    # No trailing newline: `set /p` in a .bat would swallow it into the value.
    target.write_text(sys.executable, encoding="utf-8")
    return target


def preflight(p: dict, t: dict) -> int:
    if p["python_tuple"] < MIN_PYTHON:
        print(t["py_too_old"].format(need=".".join(map(str, MIN_PYTHON)),
                                     have=p["python"], pinned=PINNED_PYTHON))
        return 1
    if p["python_tuple"] > MAX_PYTHON:
        print(t["py_too_new"].format(have=p["python"],
                                     maxv=".".join(map(str, MAX_PYTHON)),
                                     pinned=PINNED_PYTHON))
        return 1
    if not p["is_64bit"]:
        print(t["py_not_64"])
        return 1
    try:
        import pip  # noqa: F401,PLC0415
    except ImportError:
        print(t["no_pip"])
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recommended", action="store_true",
                    help="skip the menus and take the recommended selection")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the machine scan and the plan, install nothing")
    ap.add_argument("--json", action="store_true",
                    help="print the probe and recommendation as JSON and exit")
    ap.add_argument("--lang", choices=["ru", "en"])
    args = ap.parse_args()

    setup_console()
    p = probe()
    if args.json:
        print(json.dumps({"probe": p, "recommendation": recommend(p)},
                         ensure_ascii=False, indent=2))
        return 0

    lang = args.lang or pick_language()
    t = T[lang]
    print(f"\n=== {t['title']} ===")
    print(t["scanning"])
    print_machine(p, t)

    rc = preflight(p, t)
    if rc:
        return rc

    rec = recommend(p)
    print(f"\n{t['recommendation']}: "
          f"{t['torch_cuda'] if rec['torch'] == 'cuda' else t['torch_cpu']}")

    pins = desktop_pins()
    server_pins = parse_requirements(SERVER_REQ)

    if args.recommended:
        selection = {"torch": rec["torch"], "engines": rec["engines"],
                     "rag": rec["rag"], "server": rec["server"],
                     "pyannote": rec["pyannote"],
                     "models": default_models(rec["engines"],
                                              hardware_tier(p)),
                     "local_llm_model": ""}
        print(f"  {local_llm_message(p, rec, t)}")
    else:
        selection = interactive(p, rec, t, lang)

    plan = build_plan(selection, pins, server_pins)
    print_plan(plan, p, t)

    if args.plan_only:
        return 0
    if not args.yes and _ask(f"\n{t['confirm']} ").lower() != "y":
        print(t["aborted"])
        return 1
    return run_plan(plan, t)


if __name__ == "__main__":
    sys.exit(main())
