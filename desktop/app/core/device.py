"""Lazy CUDA / device probe (TODO #9).

``torch.cuda.is_available()`` needs to import torch, which is slow (seconds) and
would stall the UI — so this NEVER runs at startup or on the UI thread. It runs
in a subprocess (the bundled python) driven by ``DeviceWorker`` (core/worker.py);
the window updates its device indicator when the result arrives, and the pipeline
concurrency is recomputed (``resolve_workers(cuda=…)``: a single GPU is
VRAM-bound, so ``auto`` drops to 1 worker on CUDA).
"""
from __future__ import annotations

import json
import subprocess

# Run by the bundled python. Robust: any failure (no torch, no CUDA build) => CPU.
_PROBE_CODE = (
    "import json\n"
    "try:\n"
    "    import torch\n"
    "    c = bool(torch.cuda.is_available())\n"
    "    print(json.dumps({'cuda': c, 'name': torch.cuda.get_device_name(0) if c else None,"
    " 'torch': torch.__version__}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'cuda': False, 'name': None, 'error': str(e)[:200]}))\n"
)


def probe(python_exe, timeout: float = 90) -> dict:
    """Return ``{'cuda': bool, 'name': str|None, 'torch': str|None, 'error': str|None}``.

    Never raises — on any failure returns ``cuda=False`` with the error captured,
    so the app safely falls back to CPU.
    """
    try:
        proc = subprocess.run(
            [str(python_exe), "-c", _PROBE_CODE],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"cuda": False, "name": None, "torch": None, "error": str(exc)[:200]}
    out = (proc.stdout or "").strip()
    try:
        d = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return {"cuda": False, "name": None, "torch": None,
                "error": ((proc.stderr or out or "no output").strip())[:200]}
    return {"cuda": bool(d.get("cuda")), "name": d.get("name"),
            "torch": d.get("torch"), "error": d.get("error")}
