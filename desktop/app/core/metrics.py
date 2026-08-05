"""System resource metrics for the Diagnostics window (TODO #10).

CPU/RAM via psutil (instant, non-blocking); GPU via ``nvidia-smi`` (utilisation +
memory), gracefully ``None`` when there is no NVIDIA GPU / driver. Qt-free so it
can be unit-tested and never drags Qt into the backend.
"""
from __future__ import annotations

import shutil
import subprocess

try:
    import psutil
except Exception:   # pragma: no cover - psutil should be installed
    psutil = None


_CREATE_NO_WINDOW = 0x08000000


def _hidden_startupinfo():
    """Hidden-window STARTUPINFO on Windows so nvidia-smi does not flash a console
    every poll; None elsewhere."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def gpu_sample() -> "dict | None":
    """Return GPU utilisation/memory via nvidia-smi, or None if unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4.0,
            creationflags=_CREATE_NO_WINDOW, startupinfo=_hidden_startupinfo())
        line = (out.stdout or "").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        return {"util": int(parts[0]), "mem_used_mb": int(parts[1]),
                "mem_total_mb": int(parts[2]), "name": parts[3]}
    except Exception:
        return None


def sample() -> dict:
    """One snapshot of CPU / RAM / GPU. Missing sources come back as None.

    ``psutil.cpu_percent(interval=None)`` reports usage since the PREVIOUS call,
    so a repeating sampler yields real deltas (the first reading is ~0)."""
    cpu = psutil.cpu_percent(interval=None) if psutil else None
    vm = psutil.virtual_memory() if psutil else None
    return {
        "cpu_percent": cpu,
        "ram_percent": (vm.percent if vm else None),
        "ram_used_mb": (int(vm.used / 1048576) if vm else None),
        "ram_total_mb": (int(vm.total / 1048576) if vm else None),
        "gpu": gpu_sample(),
        "psutil": bool(psutil),
    }
