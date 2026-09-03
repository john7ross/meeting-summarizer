"""Self-test for the min-distribution interactive installer
(desktop/packaging/installer.py).

The installer runs on a BARE system Python before anything is installed, so the
value it must never lose is: correct pins, correct hardware advice, and a plan
that installs nothing the user did not pick. Everything here is offline - the
machine probe is replaced by synthetic profiles, no pip is invoked.

    backend\\python\\python.exe desktop\\_selftest_installer.py
"""
import subprocess
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "desktop" / "packaging"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import installer as I                        # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


PINS = I.desktop_pins()
SERVER_PINS = I.parse_requirements(I.SERVER_REQ)

# -- manifests parse ---------------------------------------------------
check("desktop_manifest_parsed", len(PINS) >= 20, f"{len(PINS)} packages")
check("server_manifest_parsed", len(SERVER_PINS) >= 10, f"{len(SERVER_PINS)} packages")
check("commented_optionals_excluded",
      "pyannote.audio" not in SERVER_PINS and "openai" not in PINS,
      "commented-out extras are not installed by default")
check("optin_extra_still_resolvable",
      "pyannote.audio" in PINS,
      "pyannote is pinned in backend/requirements.txt, so ticking it works")

# -- pin parity: the drift detector ------------------------------------
# Every package any group can install MUST exist in a manifest, otherwise the
# installer would pip-install an unpinned version behind the user's back.
all_group_packages = set(I.CORE_PACKAGES) | set(I.TORCH_PACKAGES) | \
    set(I.RAG_PACKAGES) | {p for pkgs in I.ENGINE_PACKAGES.values() for p in pkgs}
unpinned = sorted(p for p in all_group_packages
                  if p.lower().replace("_", "-") not in PINS)
check("every_group_package_is_pinned", not unpinned,
      "ok" if not unpinned else f"NOT IN MANIFEST: {unpinned}")

check("pyannote_group_pinned",
      all(p.lower() in PINS for p in I.PYANNOTE_PACKAGES),
      str(I.resolve(I.PYANNOTE_PACKAGES, PINS)))

try:
    I.resolve(["definitely-not-a-real-package"], PINS)
    check("unknown_package_raises", False, "no error raised")
except KeyError:
    check("unknown_package_raises", True, "drift is a hard error")

# -- every registry engine is installable ------------------------------
import engines_registry as reg               # noqa: E402
selectable = [e for e in reg.ENGINES if e != "sherpa-extra"]
uncovered = [e for e in selectable if e not in I.ENGINE_PACKAGES]
check("all_engines_have_packages", not uncovered,
      "ok" if not uncovered else f"missing: {uncovered}")
check("engine_hints_bilingual",
      all(e in I.ENGINE_HINT["ru"] and e in I.ENGINE_HINT["en"]
          for e in I.ENGINE_PACKAGES),
      f"{len(I.ENGINE_PACKAGES)} engines")

# -- synthetic machines ------------------------------------------------
def machine(vram=0.0, ram=8.0, gpu="", cores=8, disk=200.0):
    return {"python": "3.11.8", "python_tuple": (3, 11), "is_64bit": True,
            "cpu_cores": cores, "ram_gb": ram, "free_disk_gb": disk,
            "gpu": gpu or ("NVIDIA Test" if vram else ""), "vram_gb": vram,
            "driver": "999.99" if vram else ""}


workstation = machine(vram=16.0, ram=32.0)
midrange = machine(vram=6.0, ram=16.0)
cpu_box = machine(vram=0.0, ram=16.0)
weak = machine(vram=0.0, ram=4.0, cores=2, disk=12.0)

r_ws, r_mid, r_cpu, r_weak = (I.recommend(m) for m in
                              (workstation, midrange, cpu_box, weak))

check("gpu_gets_cuda_torch", r_ws["torch"] == "cuda", r_ws["torch"])
check("midrange_gpu_gets_cuda", r_mid["torch"] == "cuda", r_mid["torch"])
check("no_gpu_gets_cpu_torch", r_cpu["torch"] == "cpu", r_cpu["torch"])
check("weak_gets_cpu_torch", r_weak["torch"] == "cpu", r_weak["torch"])
check("gpu_recommends_whisperx", "whisperx" in r_ws["engines"], str(r_ws["engines"]))
check("cpu_box_avoids_whisperx", "whisperx" not in r_cpu["engines"], str(r_cpu["engines"]))
check("weak_gets_light_engines",
      set(r_weak["engines"]) <= {"vosk", "whisper-cpp", "sherpa-onnx"},
      str(r_weak["engines"]))
check("weak_skips_rag", r_weak["rag"] is False, "RAG needs >= 8 GB RAM")
check("nothing_optional_preselected",
      r_ws["server"] is False and r_ws["pyannote"] is False,
      "server/pyannote are opt-in")

# -- local-LLM advice --------------------------------------------------
check("llm_gpu_ok", r_ws["local_llm"] == "gpu", r_ws["local_llm"])
check("llm_cpu_fallback", r_cpu["local_llm"] == "cpu", r_cpu["local_llm"])
check("llm_refused_on_weak", r_weak["local_llm"] == "no", r_weak["local_llm"])

msg_ru = I.local_llm_message(weak, r_weak, I.T["ru"])
msg_en = I.local_llm_message(weak, r_weak, I.T["en"])
check("weak_llm_message_explains_why", "4" in msg_ru and "не подходит" in msg_ru)
check("weak_llm_message_offers_alternative",
      "агент" in msg_ru.lower() and "agent" in msg_en.lower(),
      "cloud provider / agent CLI suggested instead")
check("gpu_llm_message_names_model",
      any(k in I.local_llm_message(workstation, r_ws, I.T["en"])
          for k in ("qwen", "gemma")),
      I.local_llm_message(workstation, r_ws, I.T["en"])[:60])

# -- hardware tiers ----------------------------------------------------
check("tier_workstation_full", I.hardware_tier(workstation) == "full")
check("tier_midrange_medium", I.hardware_tier(midrange) == "medium")
check("tier_cpu_medium", I.hardware_tier(cpu_box) == "medium")
check("tier_weak_minimal", I.hardware_tier(weak) == "minimal")

# -- model preselection ------------------------------------------------
ws_models = I.default_models(["faster-whisper", "whisperx"], "full")
weak_models = I.default_models(["vosk", "whisper-cpp"], "minimal")
check("workstation_gets_bigger_model",
      any(m == "medium" for _, m, _ in ws_models), str(ws_models))
check("every_chosen_engine_covered",
      {"vosk", "whisper-cpp"} <= {e for e, _, _ in weak_models}
      or all(e in ("vosk", "whisper-cpp") for e, _, _ in weak_models),
      str([(e, m) for e, m, _ in weak_models]))
check("whisperx_reuses_faster_whisper_models",
      all(e != "whisperx" for e, _, _ in I.models_for(["whisperx"])),
      "whisperx has no models of its own")
check("model_sizes_never_none",
      all(isinstance(mb, int) for _, _, mb in I.models_for(list(I.ENGINE_PACKAGES))),
      "approx_mb absent -> 0, never None")

# -- plans -------------------------------------------------------------
def plan_for(sel):
    base = {"torch": "cuda", "engines": [], "rag": False, "server": False,
            "pyannote": False, "models": [], "local_llm_model": ""}
    base.update(sel)
    return I.build_plan(base, PINS, SERVER_PINS)


p_torchless = plan_for({"engines": ["vosk", "sherpa-onnx"], "torch": "cpu"})
labels = [s["label"] for s in p_torchless["steps"]]
check("torchless_engines_skip_torch", "torch" not in labels, str(labels))

p_gpu = plan_for({"engines": ["whisperx"], "torch": "cuda"})
torch_step = [s for s in p_gpu["steps"] if s["label"] == "torch"]
check("torch_step_present_for_whisperx", len(torch_step) == 1)
check("cuda_index_used",
      torch_step and torch_step[0]["index_url"] == I.TORCH_CUDA_INDEX,
      torch_step[0]["index_url"] if torch_step else "-")

p_cpu = plan_for({"engines": ["whisper"], "torch": "cpu"})
cpu_step = [s for s in p_cpu["steps"] if s["label"] == "torch"][0]
check("cpu_index_used", cpu_step["index_url"] == I.TORCH_CPU_INDEX,
      cpu_step["index_url"])

check("core_is_always_first", p_gpu["steps"][0]["label"] == "core")
check("unselected_groups_absent",
      not any(s["label"] in ("rag", "server", "pyannote") for s in p_gpu["steps"]),
      str([s["label"] for s in p_gpu["steps"]]))

p_all = plan_for({"engines": ["whisperx"], "rag": True, "server": True,
                  "pyannote": True})
check("selected_groups_present",
      {"rag", "server", "pyannote"} <= {s["label"] for s in p_all["steps"]},
      str([s["label"] for s in p_all["steps"]]))
check("server_step_uses_server_manifest",
      any("fastapi" in " ".join(s["args"]) for s in p_all["steps"]
          if s["label"] == "server"))

check("plan_size_grows_with_models",
      plan_for({"engines": ["whisper"], "models": [("whisper", "medium", 1500)]})["size_mb"]
      > p_cpu["size_mb"], "model bytes are counted")
check("plan_counts_local_llm",
      plan_for({"engines": ["whisper"], "local_llm_model": "qwen3-4b"})["size_mb"]
      > p_cpu["size_mb"], "GGUF bytes are counted")
check("no_pip_args_are_empty",
      all(s["args"] for s in p_all["steps"]), "every step installs something")

# -- preflight guards --------------------------------------------------
old = machine(); old["python_tuple"] = (3, 7); old["python"] = "3.7.9"
check("rejects_old_python", I.preflight(old, I.T["en"]) == 1)
bits = machine(); bits["is_64bit"] = False
check("rejects_32bit_python", I.preflight(bits, I.T["en"]) == 1)
check("accepts_this_python", I.preflight(I.probe(), I.T["en"]) == 0)

# A Python NEWER than the pins support must be refused BEFORE any pip work.
# Found on a clean Windows 11 VM: python.org now ships 3.13 by default, the guard
# only had a lower bound, and the install died a minute in with
# "metadata-generation-failed -> numpy" because numpy<2.0 has no cp313 wheels.
for _v in ((3, 13), (3, 14)):
    _new = machine()
    _new["python_tuple"] = _v
    _new["python"] = ".".join(map(str, _v)) + ".0"
    check(f"rejects_python_{_v[0]}_{_v[1]}",
          I.preflight(_new, I.T["en"]) == 1,
          "numpy<2.0 has no wheels there; pip would compile from source")
# ...and the boundary itself must still be accepted, or the guard is too greedy.
_edge = machine()
_edge["python_tuple"] = I.MAX_PYTHON
_edge["python"] = ".".join(map(str, I.MAX_PYTHON)) + ".0"
check("accepts_the_highest_supported_python", I.preflight(_edge, I.T["en"]) == 0,
      ".".join(map(str, I.MAX_PYTHON)))
# The refusal must NAME the way out, in both languages - a bare "unsupported" is
# what sends a recipient to the issue tracker.
for _lang in ("ru", "en"):
    _msg = I.T[_lang]["py_too_new"]
    check(f"py_too_new_message_is_actionable_{_lang}",
          "python.org" in _msg and "{pinned}" in _msg and "numpy" in _msg,
          "must say why, and where to get a supported Python")

# -- the launchers must use the interpreter the install went into ------------
# On a clean Windows 11 with 3.13 first on PATH, the install correctly used 3.11
# and RUN.bat then started 3.13: "No module named PySide6" on a good install.
import tempfile as _tf  # noqa: E402
_old_root = I.ROOT
try:
    I.ROOT = Path(_tf.mkdtemp())
    _rec = I.record_interpreter()
    _txt = _rec.read_text(encoding="utf-8")
    check("installer_records_its_interpreter", _rec.exists() and _txt == sys.executable,
          _txt[:60])
    check("interpreter_file_has_no_trailing_newline", not _txt.endswith(("\n", "\r")),
          "set /p in a .bat would swallow the newline into the value")
finally:
    I.ROOT = _old_root

# -- a fresh install must not warn about a model it never downloaded ---------
_old_root2 = I.ROOT
try:
    I.ROOT = Path(_tf.mkdtemp())
    _plan = {"models": [("faster-whisper", "small", 466)]}
    check("adopts_the_model_it_installed", I.adopt_installed_model(_plan) is True)
    _cfg = json.loads((I.ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    check("settings_point_at_the_downloaded_model",
          _cfg["whisperModel"] == "small" and _cfg["transcriptionEngine"] == "faster-whisper",
          str(_cfg))
    # a second run must NOT clobber what the user has since chosen
    (I.ROOT / "config" / "settings.json").write_text(
        json.dumps({"whisperModel": "large-v3"}), encoding="utf-8")
    check("never_overwrites_existing_settings",
          I.adopt_installed_model(_plan) is False
          and json.loads((I.ROOT / "config" / "settings.json").read_text(
              encoding="utf-8"))["whisperModel"] == "large-v3")
    check("no_models_no_settings_file", I.adopt_installed_model({"models": []}) is False)
finally:
    I.ROOT = _old_root2

sys.path.insert(0, str(Path(__file__).resolve().parent / "packaging"))
import build as _B  # noqa: E402
# NOTHING here may delete anything. This block used to remove `fp.parent` to tidy
# up the temp directory launcher_items() wrote into. When the launchers became
# real files in the repository root, that same line deleted the working tree, the
# git history and both release archives - twice, because `ignore_errors=True`
# hid it and the run merely "failed" instead of screaming. launcher_items()
# creates nothing now, so there is nothing to clean up.
_min_items, _full_items = _B.launcher_items("min"), _B.launcher_items("full")
# newline="" keeps the real line endings: universal-newline mode would rewrite
# "\r\r\n" to "\n\n" and hide exactly the defect checked for below. (Path.read_text
# only grew a newline= argument in 3.13; the bundled runtime is 3.11.)
def _raw(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


_min = {arc: _raw(fp) for fp, arc in _min_items}
_full = {arc: _raw(fp) for fp, arc in _full_items}
# min and full now ship the SAME launcher: it resolves the interpreter at run
# time, so one file serves a clone, a min install and an unpacked full build.
# RUN.bat aims at pythonw (the app has its own window); SERVER.bat at python.
for _name, _exe in (("RUN.bat", "pythonw.exe"), ("SERVER.bat", "python.exe")):
    check(f"{_name.lower()}_is_the_same_file_in_both_variants",
          _min[_name] == _full[_name],
          "two copies of a launcher drift; that is how the repo one broke")
    check(f"{_name.lower()}_prefers_the_recorded_interpreter",
          "config\\interpreter.txt" in _min[_name] and '"%PY%"' in _min[_name],
          "must not hardcode bare `python`")
    check(f"{_name.lower()}_still_falls_back_to_path",
          f'set "PY={_exe}"' in _min[_name] or 'set "PY=python"' in _min[_name],
          "a user who skipped INSTALL.bat must still get a sensible attempt")
    check(f"{_name.lower()}_prefers_the_bundled_runtime_when_present",
          _min[_name].index(f"backend\\python\\{_exe}") < _min[_name].index("interpreter.txt"),
          "a full build must use its own interpreter before anything else")

# Both READMEs document `INSTALL.bat --recommended --yes` and `--plan-only`.
# Without %* the wrapper ate them and an unattended install dropped into the
# interactive menu, then exited 1 on EOF. Caught by running the shipped archive.
check("install_bat_forwards_its_arguments",
      "installer.py %*" in _min["INSTALL.bat"],
      "--recommended/--yes/--plan-only are documented; they must reach installer.py")

# A clean Windows 11 has a zero-byte Microsoft Store stub on PATH called python.exe.
# INSTALL.bat used to invoke it blind, so the recipient's whole first-run experience
# was Windows saying "install Python from the Microsoft Store" - the Store build is
# 3.13+, which this stack rejects. Reproduced on a clean VM.
_ib = _min["INSTALL.bat"]
check("install_bat_finds_a_real_interpreter_itself",
      ":supported" in _ib and ":any" in _ib and '-c "import sys"' in _ib,
      "running a bare `python` hands the first run to the Store stub")
check("install_bat_prefers_a_supported_minor",
      _ib.index("call :supported") < _ib.index("call :any"),
      "a supported interpreter must be chosen over merely-present one")
# Bare `py` means "newest installed" - on the build box it picked 3.14 while a
# working 3.11 sat on PATH as `python`, so the whole install refused to start.
check("install_bat_does_not_prefer_the_newest_python",
      _ib.index('call :supported "python"') < _ib.index('call :supported "py"'),
      "`py` selects the newest runtime, which is the one most likely unsupported")
check("install_bat_version_gate_matches_the_installer",
      f"({I.MIN_PYTHON[0]},{I.MIN_PYTHON[1]})<=sys.version_info[:2]<="
      f"({I.MAX_PYTHON[0]},{I.MAX_PYTHON[1]})" in _ib.replace(" ", ""),
      f"the .bat must gate on installer.py's own {I.MIN_PYTHON}-{I.MAX_PYTHON}")
check("install_bat_names_the_fix_when_no_python_exists",
      ":manual" in _ib and "python.org" in _ib and "Add python.exe to PATH" in _ib,
      "the message must say where to get it, not just that it is missing")
# installer.py is a Python program, so it can never install the interpreter it
# runs on. On a clean Windows 11 there is no Python at all, which made "run
# INSTALL.bat once" untrue; the .bat offers to bootstrap one first.
check("install_bat_offers_to_install_python",
      "bootstrap_python.ps1" in _ib and "for /f" in _ib,
      "a clean machine has no interpreter and the min build promises one run")
# The wider search has to happen BEFORE the offer, or a second run proposes
# installing a Python that is already on disk but not yet on this PATH.
check("install_bat_searches_widely_before_offering_to_install",
      _ib.index("-ProbeOnly") < _ib.index(":nopython"),
      "asking first and searching afterwards proposes a redundant install")
check("install_bat_bootstrap_is_declinable",
      'if /i "%ANS%"=="n" goto :manual' in _ib,
      "installing a runtime must be refusable, with the manual route still shown")
check("install_bat_bootstrap_is_unattended_with_yes",
      "--yes" in _ib and "set /p" in _ib,
      "--yes must not sit waiting on a prompt")
_boot = (Path(__file__).resolve().parent / "packaging" / "bootstrap_python.ps1")
check("bootstrap_script_exists", _boot.is_file(), str(_boot))
_bs = _boot.read_text(encoding="utf-8") if _boot.is_file() else ""
check("bootstrap_verifies_what_it_downloads",
      "Get-AuthenticodeSignature" in _bs and "Python Software Foundation" in _bs,
      "an unverified .exe from the network must never be executed")
check("bootstrap_refuses_an_unsigned_download",
      "Remove-Item $exe" in _bs and "Nothing was installed" in _bs,
      "a failed signature check must delete the file and stop")
check("bootstrap_installs_per_user",
      "InstallAllUsers=0" in _bs and "PrependPath=1" in _bs,
      "a machine-wide install would demand elevation the recipient may not have")
# The comments legitimately NAME both old approaches, so strip them before
# looking for the code that used them.
_bs_code = "\n".join(ln.split("#", 1)[0] for ln in _bs.splitlines())
# `for /f` captures stdout, so anything printed there is invisible AND would be
# mistaken for the interpreter path. Progress must go to stderr.
check("bootstrap_progress_is_visible_through_for_f",
      "[Console]::Error.WriteLine" in _bs_code and "Write-Host" not in _bs_code,
      "Write-Host output is swallowed by the capture; the console just freezes")
# winget sat for 8 minutes on a clean VM with its output redirected, installing
# nothing and explaining nothing.
check("bootstrap_does_not_depend_on_winget",
      "winget" not in _bs_code.lower(),
      "an interactive package manager behind a pipe is not a dependency we control")
check("bootstrap_download_cannot_hang_forever",
      "-TimeoutSec" in _bs,
      "an unbounded download is indistinguishable from a hang")
# The py launcher goes machine-wide by default, which needs admin. Under /quiet
# the elevation request has nowhere to appear and the installer waits on an
# invisible consent.exe forever - observed three times on the clean VM.
check("bootstrap_never_needs_elevation",
      "InstallLauncherAllUsers=0" in _bs and "InstallAllUsers=0" in _bs,
      "an unattended install that pops UAC cannot complete")
check("bootstrap_install_cannot_hang_forever",
      "Wait-Process -Timeout" in _bs and "did not finish within" in _bs,
      "a blocked installer must fail loudly instead of hanging the console")
# PrependPath only rewrites the registry, so a console (or explorer) started
# before the install still sees the old PATH. Probing PATH alone made a second
# run of INSTALL.bat offer to install Python all over again.
check("bootstrap_looks_beyond_PATH",
      "Programs\\Python" in _bs and "config\\interpreter.txt" in _bs,
      "a freshly installed interpreter is invisible on the old PATH")
# An absolute path can contain spaces; the candidate list must not be strings
# that get split on " ".
check("bootstrap_candidates_survive_spaces_in_paths",
      "$cand.Exe" in _bs and '.Split(" ")' not in _bs,
      "splitting a command line on spaces tears an absolute path in half")
check("bootstrap_probe_has_no_install_side_effect",
      "if ($ProbeOnly) { exit 1 }" in _bs,
      "probing must be safe to run anywhere, including from this test")
# Runs the real script. A single-word command used to expand to ($null, 'python'),
# so the empty argument made the interpreter wait on stdin and the whole install
# hung with no output at all - a static check would never have seen it.
try:
    _p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", str(_boot), "-ProbeOnly"],
                        capture_output=True, text=True, timeout=120)
    _probed = (_p.stdout or "").strip().splitlines()
    check("bootstrap_probe_finishes_without_blocking", True, "")
    check("bootstrap_probe_reports_a_real_interpreter",
          _p.returncode != 0 or (_probed and Path(_probed[-1]).is_file()),
          f"rc={_p.returncode} out={_probed[-1:] } err={(_p.stderr or '')[:120]}")
except subprocess.TimeoutExpired:
    check("bootstrap_probe_finishes_without_blocking", False,
          "the probe hung - an interpreter is waiting on stdin")
    check("bootstrap_probe_reports_a_real_interpreter", False, "probe hung")
check("bootstrap_ships_in_the_min_archive",
      "desktop/packaging/bootstrap_python.ps1" in
      {arc for _, arc in _B.collect_common(Path(__file__).resolve().parents[1])},
      "INSTALL.bat calls it; shipping without it would break the clean-machine path")
check("install_bat_warns_off_the_store_build",
      "Microsoft Store" in _ib and "3.13" in _ib,
      "the Store build is exactly the trap this message exists to prevent")
check("install_bat_message_survives_the_console_codepage",
      "chcp 65001" in _ib and "Python не найден" in _ib,
      "Cyrillic in a UTF-8 .bat renders as mojibake under cp866")
# write_text() translates "\n" to os.linesep, so the "\r\n" in these strings
# shipped as "\r\r\n" - a blank line between every command in every launcher.
for _name, _bag in (("min", _min), ("full", _full)):
    for _file, _text in _bag.items():
        check(f"{_name}_{_file.lower()}_has_clean_line_endings",
              "\r\r\n" not in _text and "\r\n" in _text,
              "a doubled carriage return on every line")
for _flag in ("--recommended", "--yes", "--plan-only"):
    check(f"installer_accepts_{_flag.strip('-')}",
          _flag in (Path(__file__).resolve().parent / "packaging" /
                    "installer.py").read_text(encoding="utf-8"),
          "documented flag with no parser entry")

# -- interactive toggling ----------------------------------------------
answers = iter(["2", "", "n", "", "q"])
I._ask = lambda _prompt="": next(answers, "q")
opts = [("a", "A", ""), ("b", "B", ""), ("c", "C", "")]
check("menu_toggles_by_number",
      I._menu("t", opts, {"a"}, I.T["en"]) == {"a", "b"}, "2 toggles B on")
check("menu_none_clears", I._menu("t", opts, {"a", "b"}, I.T["en"]) == set())

# -- real probe on this machine ----------------------------------------
p = I.probe()
check("probe_has_all_keys",
      {"python", "is_64bit", "cpu_cores", "ram_gb", "free_disk_gb",
       "gpu", "vram_gb", "driver"} <= set(p), str(sorted(p)))
check("probe_ram_plausible", p["ram_gb"] > 0.5, f"{p['ram_gb']} GB")
check("probe_disk_plausible", p["free_disk_gb"] > 0, f"{p['free_disk_gb']} GB")

# -- the shipped script actually runs, and installs nothing ------------
run = subprocess.run([sys.executable, str(PROJECT_ROOT / "desktop" / "packaging"
                                          / "installer.py"),
                      "--lang", "en", "--recommended", "--plan-only"],
                     capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=180)
check("plan_only_exits_clean", run.returncode == 0, f"rc={run.returncode}")
check("plan_only_prints_plan", "Installation plan" in run.stdout)
check("plan_only_installs_nothing",
      "[Step " not in run.stdout and "Done." not in run.stdout,
      "no numbered install step ran")

# The interactive path is the one real users take: drive it end to end through
# stdin (language, three menus accepted as-is, decline the LLM download).
interactive_run = subprocess.run(
    [sys.executable, str(PROJECT_ROOT / "desktop" / "packaging" / "installer.py"),
     "--plan-only"],
    # language, engines menu (accept), PyTorch build (1 = CUDA), components menu
    # (accept), models menu (accept), decline the local-LLM download
    input="1\n\n1\n\n\nn\n", capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=300)
check("interactive_flow_completes", interactive_run.returncode == 0,
      f"rc={interactive_run.returncode} {interactive_run.stderr.strip()[-160:]}")
check("interactive_flow_reaches_plan",
      "План установки" in interactive_run.stdout, "RU plan printed")
check("interactive_flow_offers_engines_and_models",
      "Движки транскрибации" in interactive_run.stdout
      and "Модели для скачивания" in interactive_run.stdout)
check("interactive_flow_no_traceback",
      "Traceback" not in interactive_run.stderr, interactive_run.stderr[-160:])

json_run = subprocess.run([sys.executable, str(PROJECT_ROOT / "desktop"
                                               / "packaging" / "installer.py"),
                           "--json"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
check("json_mode_is_machine_readable",
      json_run.returncode == 0 and '"recommendation"' in json_run.stdout)

# -- installer ships in the min archive --------------------------------
sys.path.insert(0, str(PROJECT_ROOT / "desktop" / "packaging"))
import build as B                            # noqa: E402
arcs = [a for _, a in B.collect_common(B.ROOT)]
check("installer_is_packaged",
      "desktop/packaging/installer.py" in arcs, "ships with the distribution")


# -- the CUDA/CPU decision belongs to the user -------------------------------
# It used to be taken from the probe alone: a broken driver, a shared machine or
# simply not wanting a 2.5 GB download left the user no way out.
_src = (Path(__file__).resolve().parent / "packaging" / "installer.py").read_text(encoding="utf-8")
check("installer_asks_which_torch_build",
      '"torch_header"' in _src and 'torch_choice = rec["torch"]' in _src
      and 'return {"torch": torch_choice' in _src,
      "the interactive flow must offer CUDA vs CPU")
check("torch_question_is_bilingual",
      _src.count('"torch_header"') == 2 and _src.count('"torch_pick_cpu"') == 2
      and _src.count('"torch_pick_cuda"') == 2)
# A duplicate key inside one dict literal silently overwrites the first: the menu
# labels once shadowed the status messages that share the torch_ prefix.
import ast as _ast
_dupes = []
for _node in _ast.walk(_ast.parse(_src)):
    if isinstance(_node, _ast.Dict):
        _keys = [k.value for k in _node.keys
                 if isinstance(k, _ast.Constant) and isinstance(k.value, str)]
        _dupes += [k for k in set(_keys) if _keys.count(k) > 1]
check("no_duplicate_translation_keys", not _dupes, str(sorted(set(_dupes))))

for _mode, _expect_cuda in (("cuda", True), ("cpu", False)):
    _plan = I.build_plan({"torch": _mode, "engines": ["faster-whisper"], "rag": False,
                          "server": False, "pyannote": False, "models": [],
                          "local_llm_model": ""}, PINS, SERVER_PINS)
    _step = next(s for s in _plan["steps"] if s["label"] == "torch")
    _args = " ".join(_step["args"])
    check(f"torch_{_mode}_plan_uses_the_right_index",
          ("/cpu" in _step.get("index_url", "")) if _mode == "cpu"
          else ("cu124" in _step.get("index_url", "")),
          _step.get("index_url", ""))
    check(f"torch_{_mode}_plan_pins_match_that_index",
          ("+cu" in _args) is _expect_cuda,
          _args[:70] + " <- a +cuXXX pin does not exist on the CPU index")

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if results and all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
