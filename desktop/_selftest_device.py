"""TODO #9 — CUDA auto-detect: device probe + worker resolution + queue cap.

Runs the REAL probe against the bundled python (which has torch+CUDA in this env)
and checks resolve_workers' CUDA-aware "auto" logic and the new concurrency setter.

Run:
    backend\\python\\python.exe desktop\\_selftest_device.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # project root (parent of desktop/)
sys.path.insert(0, str(ROOT))

from desktop.app import paths
from desktop.app.core import device
from desktop.app.core.queue_manager import resolve_workers

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── real probe on the bundled python ─────────────────────────────────────────
info = device.probe(paths.python_executable())
check("probe_returns_dict", isinstance(info, dict) and "cuda" in info, str(info)[:120])
check("probe_cuda_is_bool", isinstance(info.get("cuda"), bool))
check("probe_never_raises_shape",
      set(("cuda", "name", "torch", "error")).issubset(info.keys()))
if info.get("cuda"):
    check("probe_gpu_has_name", bool(info.get("name")), str(info))
    print(f"  [info] CUDA present: {info.get('name')} (torch {info.get('torch')})")
else:
    check("probe_cpu_ok", True)
    print(f"  [info] no CUDA (error={info.get('error')})")

# ── resolve_workers: CUDA-aware "auto" + explicit passthrough ─────────────────
check("auto_cuda_is_1", resolve_workers("auto", cuda=True) == 1)
cpu_auto = resolve_workers("auto", cuda=False)
check("auto_cpu_in_range", 1 <= cpu_auto <= 4, str(cpu_auto))
check("explicit_ignores_cuda",
      resolve_workers(4, cuda=True) == 4 and resolve_workers("6", cuda=True) == 6)

# ── PipelineQueue.set_max_concurrency ────────────────────────────────────────
from PySide6.QtCore import QCoreApplication
_app = QCoreApplication.instance() or QCoreApplication([])
from desktop.app.core.pipeline import PipelineQueue
q = PipelineQueue(4, lambda eid, vp: None)
check("queue_initial_cap", q.max_concurrency == 4)
q.set_max_concurrency(1)
check("queue_cap_lowered", q.max_concurrency == 1)
q.set_max_concurrency(0)   # clamps to >=1
check("queue_cap_clamped_min", q.max_concurrency == 1)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
