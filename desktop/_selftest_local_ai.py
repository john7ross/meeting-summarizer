"""Self-test for the built-in local-AI manager (backend/local_ai.py).

Verifies the parts that must never silently rot — the curated model catalog, the
LIVE resolution of the current llama.cpp Windows build (a stale hardcoded link
is the classic failure here), hardware-based recommendation, status reporting
and the CLI contract. Nothing multi-gigabyte is downloaded.

Network-dependent checks degrade to a reported skip when offline.
    backend\\python\\python.exe desktop\\_selftest_local_ai.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import local_ai as la                    # noqa: E402
from app import paths                    # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


def skip(name, why):
    results.append(f"SKIP  {name}  {why}")


# -- catalog integrity -------------------------------------------------
check("catalog_not_empty", len(la.CATALOG) >= 3, f"{len(la.CATALOG)} models")
for key, info in la.CATALOG.items():
    check(f"catalog_fields[{key}]",
          all(k in info for k in ("label", "url", "file", "size_gb", "vram_gb", "ctx",
                                  "reasoning")))
    check(f"catalog_url_https[{key}]", info["url"].startswith("https://"))
    check(f"catalog_file_gguf[{key}]", info["file"].endswith(".gguf"), info["file"])

# The catalog must stay CURRENT — an all-Qwen2.5 list is the stale state we fixed.
check("catalog_is_fresh_not_qwen2.5",
      not all("qwen2.5" in k for k in la.CATALOG),
      "catalog looks stale (only Qwen2.5)")
check("catalog_has_multiple_families",
      len({k.split("-")[0].rstrip("0123456789.") or k for k in la.CATALOG}) >= 2,
      "only one model family offered")

# model_path resolves a CUSTOM (non-catalog) id to a file name in the models dir.
_custom = la.model_path("Some-Custom-Model-Q4_K_M.gguf")
check("model_path_custom", _custom.name == "Some-Custom-Model-Q4_K_M.gguf", str(_custom))
check("model_path_catalog",
      la.model_path(next(iter(la.CATALOG))).name.endswith(".gguf"))

# -- port isolation: must NOT default to the usual 8080 ----------------
check("default_port_not_8080", la.DEFAULT_PORT != 8080, str(la.DEFAULT_PORT))
check("endpoint_shape", la.endpoint(1234) == "http://127.0.0.1:1234/v1", la.endpoint(1234))

# -- hardware probing + recommendation ---------------------------------
rec = la.recommended_model()
check("recommended_in_catalog", rec in la.CATALOG, rec)
v = la.vram_gb()
check("vram_non_negative", v >= 0, f"{v:.1f} GB")
_smallest = min(la.CATALOG.values(), key=lambda i: i["vram_gb"])["vram_gb"]
if v >= la.CATALOG[rec]["vram_gb"] or la.CATALOG[rec]["vram_gb"] == _smallest:
    check("recommendation_fits_vram", True, f"{rec} for {v:.1f} GB")
else:
    check("recommendation_fits_vram", False, f"{rec} needs {la.CATALOG[rec]['vram_gb']} GB")

# -- status contract ---------------------------------------------------
st = la.status()
for key in ("engine_installed", "models", "running", "port", "gpu", "recommended",
            "install_dir", "vram_gb"):
    check(f"status_has[{key}]", key in st)
check("status_models_cover_catalog", set(st["models"]) == set(la.CATALOG))
check("install_dir_under_resources", "local_ai" in st["install_dir"], st["install_dir"])

# -- engine resolution is LIVE (no stale hardcoded URL) ----------------
try:
    url, name = la.resolve_engine_asset()
    check("engine_asset_resolved", name.endswith(".zip") and url.startswith("https://"), name)
    check("engine_asset_is_windows", "win" in name.lower(), name)
    check("engine_asset_not_cudart", not name.startswith("cudart"), name)
    if la.has_nvidia():
        check("engine_prefers_cuda_on_nvidia", "cuda" in name.lower(), name)
    else:
        check("engine_uses_cpu_without_nvidia", "cpu" in name.lower(), name)
except Exception as exc:                 # noqa: BLE001
    skip("engine_asset_resolved", f"offline/API unavailable: {exc}")

# -- CLI contract (status + catalog emit one JSON line with 'result') --
def cli(*args):
    proc = subprocess.run(
        [str(paths.python_executable()), str(ROOT / "backend" / "local_ai.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    line = (proc.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


for cmd in ("status", "catalog"):
    try:
        payload = cli(cmd)
        check(f"cli_{cmd}_done", payload.get("event") == "done", str(payload.get("event")))
        check(f"cli_{cmd}_result", isinstance(payload.get("result"), dict))
    except Exception as exc:             # noqa: BLE001
        check(f"cli_{cmd}_done", False, str(exc)[:120])

# -- guard rails -------------------------------------------------------
try:
    la.install_model("no-such-model")
    check("reject_unknown_model", False, "accepted an unknown model id")
except ValueError:
    check("reject_unknown_model", True)
except Exception as exc:                 # noqa: BLE001
    check("reject_unknown_model", False, type(exc).__name__)

try:                                     # starting without an installed engine must fail loudly
    if not la.server_exe().exists():
        la.start(rec, port=59997, wait=1)
        check("start_without_engine_fails", False, "started with no engine")
    else:
        skip("start_without_engine_fails", "engine already installed")
except RuntimeError:
    check("start_without_engine_fails", True)
except Exception as exc:                 # noqa: BLE001
    check("start_without_engine_fails", False, type(exc).__name__)

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
