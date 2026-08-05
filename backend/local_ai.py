"""Built-in local AI — an optional, downloaded-on-demand llama.cpp server.

The point: a user who knows nothing about LLMs should still get the full feature
set (summary + analysis) without installing anything by hand. This module can

  * resolve and download the CURRENT llama.cpp Windows build (via the GitHub
    releases API, so the link never goes stale), CUDA or CPU as appropriate,
  * download a curated GGUF chat model sized to the machine,
  * start/stop that server locally and report its health.

It is deliberately NOT part of the full distribution (several GB); everything
lands under ``resources/local_ai/`` on demand.

CLI (JSON-lines progress, same protocol as models_cli.py so the existing
ModelsWorker can drive it)::

    python local_ai.py status
    python local_ai.py install --model qwen2.5-7b
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_model import _http_download_resume     # noqa: E402  (resumable, proven)

ROOT = Path(__file__).resolve().parent.parent
INSTALL_DIR = ROOT / "resources" / "local_ai"
ENGINE_DIR = INSTALL_DIR / "engine"
MODELS_DIR = INSTALL_DIR / "models"
STATE_FILE = INSTALL_DIR / "state.json"

GITHUB_LATEST = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
# 8081, not 8080: a user who already runs their own llama.cpp/LM Studio on the
# usual port must not have it clobbered by the built-in server.
DEFAULT_PORT = 8081
_CREATE_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008

# Curated, verified single-file GGUF chat models (multilingual, good Russian).
# URLs HEAD-checked live (mid-2026). 'vram_gb' is the rough amount to run fully on
# the GPU; 'reasoning' models "think out loud" and are slow unless the app's
# "disable reasoning" is on (the built-in-AI dialog turns it on for them by default).
# A user can also point at ANY other GGUF by URL (see install_model(url=...)).
DEFAULT_CTX = 16384

CATALOG = {
    "qwen3-4b": {
        "label": "Qwen3 4B (компактная, ~2.3 ГБ)",
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3-4B-GGUF/resolve/main/Qwen_Qwen3-4B-Q4_K_M.gguf",
        "file": "Qwen_Qwen3-4B-Q4_K_M.gguf", "size_gb": 2.3, "vram_gb": 4, "ctx": DEFAULT_CTX,
        "reasoning": True,
    },
    "qwen3-8b": {
        "label": "Qwen3 8B (рекомендуется, ~4.7 ГБ)",
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3-8B-GGUF/resolve/main/Qwen_Qwen3-8B-Q4_K_M.gguf",
        "file": "Qwen_Qwen3-8B-Q4_K_M.gguf", "size_gb": 4.7, "vram_gb": 7, "ctx": DEFAULT_CTX,
        "reasoning": True,
    },
    "qwen3-14b": {
        "label": "Qwen3 14B (качественнее, ~8.4 ГБ)",
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3-14B-GGUF/resolve/main/Qwen_Qwen3-14B-Q4_K_M.gguf",
        "file": "Qwen_Qwen3-14B-Q4_K_M.gguf", "size_gb": 8.4, "vram_gb": 11, "ctx": DEFAULT_CTX,
        "reasoning": True,
    },
    "qwen3-30b-a3b": {
        "label": "Qwen3 30B-A3B (MoE, быстрая при 20+ ГБ VRAM, ~17 ГБ)",
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3-30B-A3B-GGUF/resolve/main/Qwen_Qwen3-30B-A3B-Q4_K_M.gguf",
        "file": "Qwen_Qwen3-30B-A3B-Q4_K_M.gguf", "size_gb": 17.4, "vram_gb": 20, "ctx": DEFAULT_CTX,
        "reasoning": True,
    },
    "gemma3-4b": {
        "label": "Gemma 3 4B (Google, без reasoning, ~2.3 ГБ)",
        "url": "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf",
        "file": "google_gemma-3-4b-it-Q4_K_M.gguf", "size_gb": 2.3, "vram_gb": 4, "ctx": DEFAULT_CTX,
        "reasoning": False,
    },
    "gemma3-12b": {
        "label": "Gemma 3 12B (Google, без reasoning, ~6.8 ГБ)",
        "url": "https://huggingface.co/bartowski/google_gemma-3-12b-it-GGUF/resolve/main/google_gemma-3-12b-it-Q4_K_M.gguf",
        "file": "google_gemma-3-12b-it-Q4_K_M.gguf", "size_gb": 6.8, "vram_gb": 9, "ctx": DEFAULT_CTX,
        "reasoning": False,
    },
}


# -- helpers -----------------------------------------------------------
def _emit(event: str, **kw) -> None:
    print(json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def _hidden_startupinfo():
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def port_open(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def has_nvidia() -> bool:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=_CREATE_NO_WINDOW,
                             startupinfo=_hidden_startupinfo())
        return out.returncode == 0 and bool((out.stdout or "").strip())
    except Exception:      # noqa: BLE001
        return False


def vram_gb() -> float:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=_CREATE_NO_WINDOW,
                             startupinfo=_hidden_startupinfo())
        return int((out.stdout or "0").strip().splitlines()[0]) / 1024.0
    except Exception:      # noqa: BLE001
        return 0.0


def recommended_model() -> str:
    """Largest curated model that fits comfortably in this machine's VRAM."""
    v = vram_gb()
    # Sort the catalog by VRAM need, biggest first; pick the biggest that fits.
    by_size = sorted(CATALOG.items(), key=lambda kv: kv[1]["vram_gb"], reverse=True)
    for key, info in by_size:
        if v >= info["vram_gb"]:
            return key
    return by_size[-1][0]        # CPU / tiny GPU → the smallest model


def server_exe() -> Path:
    """Path to llama-server inside the extracted engine (search: layout varies)."""
    if not ENGINE_DIR.exists():
        return ENGINE_DIR / "llama-server.exe"
    hits = list(ENGINE_DIR.rglob("llama-server.exe"))
    return hits[0] if hits else ENGINE_DIR / "llama-server.exe"


def model_path(model_id: str) -> Path:
    if model_id in CATALOG:
        return MODELS_DIR / CATALOG[model_id]["file"]
    return MODELS_DIR / model_id            # custom: the id IS the .gguf file name


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def status(port: int = DEFAULT_PORT) -> dict:
    st = _read_state()
    return {
        "engine_installed": server_exe().exists(),
        "engine_path": str(server_exe()),
        "models": {k: model_path(k).exists() for k in CATALOG},
        "running": port_open(port),
        "port": port,
        "pid": st.get("pid"),
        "model_id": st.get("model_id", ""),
        "gpu": has_nvidia(),
        "vram_gb": round(vram_gb(), 1),
        "recommended": recommended_model(),
        "install_dir": str(INSTALL_DIR),
    }


# -- install -----------------------------------------------------------
def resolve_engine_asset(prefer_cuda: bool = True) -> tuple:
    """(download_url, filename) of the current llama.cpp Windows build. Resolved
    live from the GitHub releases API, so it cannot go stale."""
    resp = requests.get(GITHUB_LATEST, timeout=30)
    resp.raise_for_status()
    assets = resp.json().get("assets", [])
    names = {a["name"]: a["browser_download_url"] for a in assets}
    want_cuda = prefer_cuda and has_nvidia()
    order = (["bin-win-cuda-12.4-x64", "bin-win-cuda", "bin-win-cpu-x64"]
             if want_cuda else ["bin-win-cpu-x64"])
    for token in order:
        for name, url in names.items():
            if token in name and name.endswith(".zip") and not name.startswith("cudart"):
                return url, name
    raise RuntimeError("No suitable llama.cpp Windows build found in the latest release")


def _cudart_asset() -> tuple:
    """CUDA runtime DLLs that ship beside the CUDA build (needed to launch)."""
    resp = requests.get(GITHUB_LATEST, timeout=30)
    resp.raise_for_status()
    for a in resp.json().get("assets", []):
        if a["name"].startswith("cudart-") and "12.4" in a["name"] and a["name"].endswith(".zip"):
            return a["browser_download_url"], a["name"]
    return "", ""


def install_engine(on_progress=None) -> str:
    """Download + extract the llama.cpp server. Returns the llama-server path."""
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    url, name = resolve_engine_asset()
    archive = ENGINE_DIR / name
    _http_download_resume(url, str(archive), on_progress or (lambda *a, **k: None))
    with zipfile.ZipFile(archive) as z:
        z.extractall(ENGINE_DIR)
    archive.unlink(missing_ok=True)
    if "cuda" in name:                       # CUDA build needs the runtime DLLs
        curl, cname = _cudart_asset()
        if curl:
            carc = ENGINE_DIR / cname
            _http_download_resume(curl, str(carc), on_progress or (lambda *a, **k: None))
            with zipfile.ZipFile(carc) as z:
                z.extractall(server_exe().parent if server_exe().exists() else ENGINE_DIR)
            carc.unlink(missing_ok=True)
    exe = server_exe()
    if not exe.exists():
        raise RuntimeError("llama-server.exe not found after extraction")
    return str(exe)


def install_model(model_id: str, on_progress=None, url: str = "") -> str:
    """Download a curated model by id, OR any GGUF by *url* (custom). Returns the
    local file path; resumes a partial download."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    cb = on_progress or (lambda *a, **k: None)
    if url:                                 # custom GGUF from any HuggingFace/URL
        fname = url.split("?")[0].split("/")[-1] or "custom.gguf"
        if not fname.lower().endswith(".gguf"):
            raise ValueError("The URL must point to a single .gguf file")
        dest = MODELS_DIR / fname
        if not dest.exists():
            _http_download_resume(url, str(dest), cb)
        return str(dest)
    if model_id not in CATALOG:
        raise ValueError(f"Unknown model: {model_id}")
    dest = model_path(model_id)
    if dest.exists():
        return str(dest)
    _http_download_resume(CATALOG[model_id]["url"], str(dest), cb)
    return str(dest)


# -- run ---------------------------------------------------------------
def start(model_id: str, port: int = DEFAULT_PORT, ctx: int = 0,
          gpu_layers: int = -1, wait: float = 180.0) -> dict:
    """Launch llama-server for *model_id*; wait until it answers. Returns state."""
    exe, mdl = server_exe(), model_path(model_id)
    if not exe.exists():
        raise RuntimeError("Local AI engine is not installed")
    if not mdl.exists():
        raise RuntimeError(f"Model {model_id} is not downloaded")
    if port_open(port):
        raise RuntimeError(f"Port {port} is already in use")
    ctx = ctx or (CATALOG[model_id]["ctx"] if model_id in CATALOG else DEFAULT_CTX)
    cmd = [str(exe), "-m", str(mdl), "--host", "127.0.0.1", "--port", str(port),
           "-c", str(ctx), "--no-webui"]
    if has_nvidia():
        cmd += ["-ngl", str(gpu_layers)]
    proc = subprocess.Popen(cmd, cwd=str(exe.parent), close_fds=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_DETACHED | _CREATE_NO_WINDOW,
                            startupinfo=_hidden_startupinfo())
    deadline = time.time() + wait
    while time.time() < deadline:
        if port_open(port):
            state = {"pid": proc.pid, "port": port, "model_id": model_id,
                     "started_at": time.time()}
            _write_state(state)
            return state
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited during startup (check VRAM/model)")
        time.sleep(1.0)
    raise RuntimeError("Local AI server did not become ready in time")


def stop(port: int = DEFAULT_PORT) -> bool:
    """Stop the managed server (by recorded pid, else by port)."""
    st = _read_state()
    pid = st.get("pid")
    killed = False
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           timeout=15, creationflags=_CREATE_NO_WINDOW,
                           startupinfo=_hidden_startupinfo())
            killed = True
        except Exception:      # noqa: BLE001
            pass
    _write_state({})
    return killed or not port_open(port)


def endpoint(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/v1"


# -- CLI ---------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Built-in local AI manager")
    ap.add_argument("command", choices=["status", "install", "start", "stop", "catalog"])
    ap.add_argument("--model", default="")
    ap.add_argument("--url", default="", help="Custom GGUF URL (bypasses the curated catalog)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    try:
        if args.command == "status":
            _emit("done", result=status(args.port))
        elif args.command == "catalog":
            _emit("done", result={"catalog": CATALOG, "recommended": recommended_model()})
        elif args.command == "install":
            # Custom URL wins; otherwise a catalog id (or the recommended one).
            model = args.model or (recommended_model() if not args.url else "")
            if not server_exe().exists():
                _emit("progress", percent=0, detail="Загрузка движка llama.cpp…")
                install_engine(lambda pct, det="": _emit(
                    "progress", percent=int(pct * 0.2), detail=det or "Движок…"))
            _emit("progress", percent=20, detail=f"Загрузка модели {model or args.url}…")
            path = install_model(model, lambda pct, det="": _emit(
                "progress", percent=20 + int(pct * 0.8), detail=det or "Модель…"),
                url=args.url)
            # For a custom model the runnable id is its file name.
            model_id = model if model in CATALOG else Path(path).name
            _emit("done", result={"model_id": model_id, **status(args.port)})
        elif args.command == "start":
            model = args.model or recommended_model()
            _emit("done", result=start(model, args.port))
        elif args.command == "stop":
            _emit("done", result={"stopped": stop(args.port)})
    except Exception as exc:      # noqa: BLE001
        _emit("error", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
