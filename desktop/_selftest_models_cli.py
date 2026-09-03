"""TODO #14a — model download/update plumbing, validated WITHOUT pulling any
model over the network.

Two layers:
  - models_cli.py run as a real subprocess for the query commands (engines/list/
    available/resolve) + the not-implemented download guard (expects exit 1);
  - download_model.{plan,download,check_update} imported directly for the pure,
    no-network behaviour (plan, already-present short-circuit, implemented guard,
    whisper/vosk check_update which are static).

The actual network downloads (whisper .pt / HF snapshot / vosk zip) are NOT
exercised here — they run when the user clicks Download (TODO #14b) or at the
live run (#11).

Run:
    backend\\python\\python.exe desktop\\_selftest_models_cli.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import engines_registry as reg
import download_model as dl

CLI = str(ROOT / "backend" / "models_cli.py")

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


def run_cli(*args):
    """Run models_cli.py; return (returncode, last_json_object)."""
    p = subprocess.run([sys.executable, CLI, *args],
                       capture_output=True, text=True, timeout=60)
    last = None
    for line in p.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                pass
    return p.returncode, last


# ── CLI: engines ──────────────────────────────────────────────────────────────
rc, data = run_cli("engines")
emap = {e["id"]: e for e in (data or {}).get("engines", [])}
check("cli_engines_rc0", rc == 0)
check("cli_engines_all_present",
      {"whisper", "faster-whisper", "whisperx", "vosk", "sherpa-onnx"} <= set(emap))
check("cli_whisper_implemented", emap.get("whisper", {}).get("implemented") is True)
check("cli_sherpa_implemented", emap.get("sherpa-onnx", {}).get("implemented") is True)

# ── CLI: catalog (one-shot snapshot for the UI) ───────────────────────────────
rc, data = run_cli("catalog")
cmap = {e["id"]: e for e in (data or {}).get("engines", [])}
check("cli_catalog_rc0", rc == 0)
check("cli_catalog_all_engines",
      {"whisper", "faster-whisper", "whisperx", "vosk", "sherpa-onnx"} <= set(cmap))
check("cli_catalog_whisper_models",
      {m["id"] for m in cmap.get("whisper", {}).get("models", [])}
      == {"tiny", "base", "small", "medium", "large"})
check("cli_catalog_availability",
      any(m["id"] == "medium" and m["available"]
          for m in cmap.get("whisper", {}).get("models", [])))
check("cli_catalog_sherpa_models",
      cmap.get("sherpa-onnx", {}).get("implemented") is True
      and len(cmap.get("sherpa-onnx", {}).get("models", [])) == 4)

# ── CLI: list (engine-aware + availability) ───────────────────────────────────
rc, data = run_cli("list", "--engine", "vosk", "--language", "ru")
vmodels = {m["id"]: m for m in (data or {}).get("models", [])}
check("cli_list_vosk_ru_two", set(vmodels) == {"vosk-model-ru-0.22", "vosk-model-small-ru-0.22"},
      detail=str(set(vmodels)))
check("cli_list_vosk_ru_available", all(m["available"] and m["path"] for m in vmodels.values()))

rc, data = run_cli("list", "--engine", "whisper")
wmodels = {m["id"]: m for m in (data or {}).get("models", [])}
check("cli_list_whisper_five", set(wmodels) == {"tiny", "base", "small", "medium", "large"})
check("cli_list_whisper_avail_matches_disk",
      wmodels["tiny"]["available"] and wmodels["medium"]["available"]
      and not wmodels["small"]["available"] and not wmodels["large"]["available"])

# ── CLI: available / resolve ──────────────────────────────────────────────────
rc, data = run_cli("available", "--engine", "faster-whisper", "--model", "medium")
check("cli_available_faster_medium", (data or {}).get("available") is True and (data or {}).get("path"))
rc, data = run_cli("resolve", "--engine", "sphinx", "--model", "x")
check("cli_resolve_bogus_null", (data or {}).get("path") is None)

# ── CLI: download guard for an unknown model (exit 1 + error) ─────────────────
rc, data = run_cli("download", "--engine", "sherpa-onnx", "--model", "anything")
check("cli_download_unknown_rc1", rc == 1)
check("cli_download_unknown_error",
      (data or {}).get("ok") is False and "unknown model" in (data or {}).get("error", ""))

# ── download_model.plan (pure, no network) ────────────────────────────────────
p = dl.plan("whisper", "medium")
check("plan_whisper_medium_already", p["already"] is True and p["method"] == "whisper_lib"
      and p["target"].endswith("medium.pt"), detail=str(p))
p = dl.plan("whisper", "small")
check("plan_whisper_small_absent", p["already"] is False and p["target"].endswith("small.pt"))
p = dl.plan("faster-whisper", "large")
check("plan_faster_large_repo", p["method"] == "faster_lib"
      and p["source"] == "hf:Systran/faster-whisper-large-v3"
      and p["target"].endswith("models--Systran--faster-whisper-large-v3"), detail=str(p))
p = dl.plan("whisperx", "medium")
check("plan_whisperx_is_faster", p["method"] == "faster_lib" and p["already"] is True
      and p["target"] == reg.intended_path("faster-whisper", "medium"))
p = dl.plan("vosk", "vosk-model-ru-0.22")
check("plan_vosk_zip_source", p["method"] == "vosk_zip"
      and p["source"].endswith("vosk-model-ru-0.22.zip")
      and p["target"].endswith("vosk-model-ru-0.22") and p["already"] is True)

raised = False
try:
    dl.plan("nope-engine", "x")
except ValueError:
    raised = True
check("plan_unknown_engine_raises", raised)
raised = False
try:
    dl.plan("whisper", "nope-size")
except ValueError:
    raised = True
check("plan_unknown_model_raises", raised)

# ── download short-circuit when present (NO network) ──────────────────────────
seen = []
got = dl.download("whisper", "medium", on_progress=lambda pct, d: seen.append(d))
check("download_present_shortcircuits",
      got == reg.resolve_model_path("whisper", "medium") and "already present" in seen,
      detail=str((got, seen)))

# ── download refuses an unknown model ─────────────────────────────────────────
raised = False
try:
    dl.download("sherpa-onnx", "anything")
except ValueError:
    raised = True
check("download_unknown_model_raises", raised)

# ── check_update: honest static answers (no network for whisper/vosk) ─────────
cu = dl.check_update("whisper", "medium")
check("checkupdate_whisper_static", cu["supported"] is False)
cu = dl.check_update("vosk", "vosk-model-ru-0.22")
check("checkupdate_vosk_static", cu["supported"] is False and "alphacephei" in cu["detail"])

# ── intended_path (registry) ──────────────────────────────────────────────────
check("intended_whisper_large_v3", reg.intended_path("whisper", "large").endswith("large-v3.pt"))
check("intended_whisperx_eq_faster",
      reg.intended_path("whisperx", "medium") == reg.intended_path("faster-whisper", "medium"))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
