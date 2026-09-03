"""Per-engine model download / update — registry-driven (TODO #14a).

Rebuilt from scratch (the old Electron-era downloader is preserved in
``meeting-summarizer_old``). The PySide client uses this via ``models_cli.py``.
No single unified loader (owner's decision) — each engine keeps its OWN source:
  whisper            -> the openai-whisper downloader (SHA-verified .pt)
  faster-whisper /   -> huggingface_hub.snapshot_download (Systran/faster-whisper-*)
    whisperx            into whisper_models/ as the HF cache layout it already uses
  vosk               -> alphacephei .zip with HTTP-range resume, then extract

Heavy deps (whisper/torch, huggingface_hub, requests) are imported LAZILY inside
the branch that needs them, so importing this module (and ``plan`` / ``check_update``
for whisper/vosk) stays cheap. ``plan`` is pure (no network) and is what the
selftest exercises; the real network downloads run when the user clicks Download
(TODO #14b) or at the live run (#11).
"""
from __future__ import annotations

import os
import sys

# Match the rest of the backend: never route model fetches through a proxy.
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[_k] = ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # backend/
import engines_registry as reg


def plan(engine: str, model: str, language=None, res_dir=None) -> dict:
    """Pure (no network): what a download WOULD do — method, source, target,
    and whether the model is already present."""
    e = reg.ENGINES.get(engine)
    if not e:
        raise ValueError(f"unknown engine: {engine}")
    method = e.get("download")
    target = reg.intended_path(engine, model, res_dir)
    if target is None:
        raise ValueError(f"unknown model '{model}' for engine '{engine}'")
    if method == "whisper_lib":
        source = f"openai-whisper:{model}"
    elif method == "faster_lib":
        hf = "large-v3" if model == "large" else model
        source = f"hf:Systran/faster-whisper-{hf}"
    elif method == "vosk_zip":
        source = reg.vosk_download_url(model)
    elif method == "sherpa_targz":
        source = reg.sherpa_download_url(model)
    elif method == "whispercpp_ggml":
        source = reg.whispercpp_download_url(model)
    elif method == "funasr_targz":
        source = reg.sherpa_download_url(model)
    elif method == "sherpa_extra_targz":
        source = reg.sherpa_download_url(model)
    else:
        source = None
    return {"engine": engine, "model": model, "method": method,
            "source": source, "target": target,
            "already": reg.is_available(engine, model, language, res_dir)}


def _http_download_resume(url: str, dest: str, on_progress) -> None:
    """Download ``url`` to ``dest`` with HTTP-range resume into a .part file."""
    import requests
    part = dest + ".part"
    existing = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) + existing
        with open(part, "ab" if existing else "wb") as f:
            done = existing
            for chunk in r.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = min(90, 2 + int(done * 88 / total))
                    on_progress(pct, f"Downloading… {done >> 20}/{total >> 20} MB")
    os.replace(part, dest)


def _download_vosk(name: str, res_dir, on_progress) -> str:
    import zipfile
    root = os.path.join(res_dir or reg.resources_dir(), "vosk_models")
    os.makedirs(root, exist_ok=True)
    zip_path = os.path.join(root, name + ".zip")
    on_progress(2, f"Fetching {name}…")
    _http_download_resume(reg.vosk_download_url(name), zip_path, on_progress)
    on_progress(92, "Extracting…")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(root)
    try:
        os.remove(zip_path)
    except OSError:
        pass
    target = os.path.join(root, name)
    if os.path.isdir(os.path.join(target, "conf")):
        on_progress(100, "done")
        return target
    for entry in sorted(os.listdir(root)):       # fallback: differently-named top dir
        cand = os.path.join(root, entry)
        if os.path.isdir(os.path.join(cand, "conf")):
            on_progress(100, "done")
            return cand
    raise RuntimeError(f"Vosk archive for {name} contained no valid model (no 'conf/').")


def _download_sherpa(name: str, res_dir, on_progress) -> str:
    import tarfile
    root = os.path.join(res_dir or reg.resources_dir(), "sherpa_models")
    os.makedirs(root, exist_ok=True)
    archive = os.path.join(root, name + ".tar.bz2")
    on_progress(2, f"Fetching {name}…")
    _http_download_resume(reg.sherpa_download_url(name), archive, on_progress)
    on_progress(92, "Extracting…")
    with tarfile.open(archive, "r:bz2") as tf:
        tf.extractall(root)
    try:
        os.remove(archive)
    except OSError:
        pass
    target = os.path.join(root, name)   # k2-fsa archives extract to <name>/
    if reg._is_sherpa_model_dir(target):
        on_progress(100, "done")
        return target
    for entry in sorted(os.listdir(root)):
        cand = os.path.join(root, entry)
        if reg._is_sherpa_model_dir(cand):
            on_progress(100, "done")
            return cand
    raise RuntimeError(f"sherpa-onnx archive for {name} had no valid transducer model.")


def _download_funasr(name: str, res_dir, on_progress) -> str:
    """SenseVoice/Paraformer archives are the same k2-fsa .tar.bz2 as sherpa-onnx;
    extract to funasr_models/ and validate (tokens.txt + model[.int8].onnx)."""
    import tarfile
    root = os.path.join(res_dir or reg.resources_dir(), "funasr_models")
    os.makedirs(root, exist_ok=True)
    archive = os.path.join(root, name + ".tar.bz2")
    on_progress(2, f"Fetching {name}…")
    _http_download_resume(reg.sherpa_download_url(name), archive, on_progress)
    on_progress(92, "Extracting…")
    with tarfile.open(archive, "r:bz2") as tf:
        tf.extractall(root)
    try:
        os.remove(archive)
    except OSError:
        pass
    target = os.path.join(root, name)
    if reg._is_funasr_model_dir(target):
        on_progress(100, "done")
        return target
    for entry in sorted(os.listdir(root)):
        cand = os.path.join(root, entry)
        if reg._is_funasr_model_dir(cand):
            on_progress(100, "done")
            return cand
    raise RuntimeError(f"FunASR archive for {name} had no valid model (no model*.onnx + tokens.txt).")


def _download_sherpa_extra(name: str, res_dir, on_progress) -> str:
    """Optional community models (GigaAM/Moonshine): same k2-fsa .tar.bz2 source;
    extract to sherpa_extra_models/ and validate per the model's architecture."""
    import tarfile
    root = os.path.join(res_dir or reg.resources_dir(), "sherpa_extra_models")
    os.makedirs(root, exist_ok=True)
    archive = os.path.join(root, name + ".tar.bz2")
    mtype = reg.ENGINES["sherpa-extra"]["models"].get(name, {}).get("model_type", "")
    on_progress(2, f"Fetching {name}…")
    _http_download_resume(reg.sherpa_download_url(name), archive, on_progress)
    on_progress(92, "Extracting…")
    with tarfile.open(archive, "r:bz2") as tf:
        tf.extractall(root)
    try:
        os.remove(archive)
    except OSError:
        pass
    target = os.path.join(root, name)
    if reg._is_extra_model_dir(target, mtype):
        on_progress(100, "done")
        return target
    for entry in sorted(os.listdir(root)):
        cand = os.path.join(root, entry)
        if reg._is_extra_model_dir(cand, mtype):
            on_progress(100, "done")
            return cand
    raise RuntimeError(f"Extra model archive for {name} had no valid model files.")


def _download_whispercpp(size: str, res_dir, on_progress) -> str:
    root = os.path.join(res_dir or reg.resources_dir(), "whispercpp_models")
    os.makedirs(root, exist_ok=True)
    fname = reg.ENGINES["whisper-cpp"]["models"][size]["file"]
    dest = os.path.join(root, fname)
    on_progress(2, f"Fetching {fname}…")
    _http_download_resume(reg.whispercpp_download_url(size), dest, on_progress)
    on_progress(100, "done")
    return dest


def _download_whisper(size: str, res_dir, on_progress) -> str:
    import whisper
    root = os.path.join(res_dir or reg.resources_dir(), "whisper_models")
    os.makedirs(root, exist_ok=True)
    if size not in whisper._MODELS:
        raise ValueError(f"unknown whisper model: {size}")
    on_progress(5, f"Downloading whisper '{size}' (OpenAI, SHA-verified)…")
    path = whisper._download(whisper._MODELS[size], root, in_memory=False)
    on_progress(100, "done")
    return path


def _download_faster(size: str, res_dir, on_progress) -> str:
    from huggingface_hub import snapshot_download
    hf = "large-v3" if size == "large" else size
    repo = f"Systran/faster-whisper-{hf}"
    cache = os.path.join(res_dir or reg.resources_dir(), "whisper_models")
    os.makedirs(cache, exist_ok=True)
    on_progress(5, f"Downloading faster-whisper '{hf}' (HuggingFace)…")
    snapshot_download(repo_id=repo, cache_dir=cache)
    on_progress(100, "done")
    return os.path.join(cache, f"models--Systran--faster-whisper-{hf}")


def download(engine: str, model: str, language=None, res_dir=None,
             on_progress=None, force: bool = False) -> str:
    """Fetch ``model`` for ``engine`` to its intended path; return that path.

    Refuses engines without a transcribe adapter (``implemented=False``) so a
    user can never download a model they cannot use (TODO #14d guard). Skips the
    download when the model is already present unless ``force``.
    """
    on_progress = on_progress or (lambda *_: None)
    if not reg.is_implemented(engine):
        raise RuntimeError(
            f"engine '{engine}' has no transcribe adapter yet — refusing to "
            f"download an unusable model (see TODO #14d).")
    p = plan(engine, model, language, res_dir)
    if p["already"] and not force:
        on_progress(100, "already present")
        return p["target"]
    method = p["method"]
    if method == "whisper_lib":
        return _download_whisper(model, res_dir, on_progress)
    if method == "faster_lib":
        return _download_faster(model, res_dir, on_progress)
    if method == "vosk_zip":
        return _download_vosk(model, res_dir, on_progress)
    if method == "sherpa_targz":
        return _download_sherpa(model, res_dir, on_progress)
    if method == "whispercpp_ggml":
        return _download_whispercpp(model, res_dir, on_progress)
    if method == "funasr_targz":
        return _download_funasr(model, res_dir, on_progress)
    if method == "sherpa_extra_targz":
        return _download_sherpa_extra(model, res_dir, on_progress)
    raise ValueError(f"no download method for engine '{engine}'")


def _local_hf_rev(model_dir) -> "str | None":
    if not model_dir:
        return None
    snap = os.path.join(model_dir, "snapshots")
    if os.path.isdir(snap):
        revs = os.listdir(snap)
        return revs[0] if revs else None
    return None


def check_update(engine: str, model: str, res_dir=None) -> dict:
    """Per-engine update check. whisper/vosk are not in-place updatable (honest);
    faster-whisper/whisperx compare the local HF snapshot revision to the latest
    on the Hub (network)."""
    e = reg.ENGINES.get(engine)
    if not e:
        raise ValueError(f"unknown engine: {engine}")
    method = e.get("download")
    if method == "whisper_lib":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "OpenAI Whisper models are version-pinned (SHA-checked); no in-place update."}
    if method == "vosk_zip":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "Vosk models are versioned by name; a newer version is a "
                          "separate model — see https://alphacephei.com/vosk/models"}
    if method == "sherpa_targz":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "sherpa-onnx models are versioned by name; a newer version "
                          "is a separate model — see the k2-fsa/sherpa-onnx releases."}
    if method == "whispercpp_ggml":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "whisper.cpp ggml models are version-pinned by name; re-download "
                          "the .bin to refresh — see huggingface.co/ggerganov/whisper.cpp."}
    if method == "funasr_targz":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "FunASR (SenseVoice/Paraformer) models are versioned by name; a "
                          "newer version is a separate model — see the k2-fsa/sherpa-onnx releases."}
    if method == "sherpa_extra_targz":
        return {"engine": engine, "model": model, "supported": False,
                "detail": "Extra community models are versioned by name; a newer version is a "
                          "separate model — see the k2-fsa/sherpa-onnx releases (asr-models)."}
    if method == "faster_lib":
        from huggingface_hub import HfApi
        hf = "large-v3" if model == "large" else model
        repo = f"Systran/faster-whisper-{hf}"
        latest = HfApi().model_info(repo).sha
        local = _local_hf_rev(reg.intended_path(engine, model, res_dir))
        return {"engine": engine, "model": model, "supported": True,
                "local_rev": local, "latest_rev": latest,
                "update_available": bool(local and latest and local != latest)}
    return {"engine": engine, "model": model, "supported": False, "detail": "unknown method"}


if __name__ == "__main__":
    # This module is a library used by models_cli.py. The old Electron-era
    # `--model/--output-dir` CLI was retired (kept in meeting-summarizer_old).
    import json
    print(json.dumps({"error": "use models_cli.py (download --engine E --model M)"}))
    raise SystemExit(2)
