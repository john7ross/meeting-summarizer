"""Optional GPU hand-off — free the GPU for transcription by stopping the local LLM.

On a single GPU, a large resident LLM (e.g. a 22 GB MoE) leaves no room for the
transcription engine (~3 GB). When ``gpuHandoff`` is enabled the app, before GPU
transcription:

  1. (best-effort, time-bounded) records the command line of whatever listens on
     the LLM port, so it can be restarted afterwards,
  2. drops a lock file (so an external watchdog, if the user runs one, stands
     down and does not fight the hand-off), and
  3. stops that process, freeing VRAM.

After transcription it removes the lock and, if nothing else brought the model
back within a short grace window, restarts it from the recorded command line.
Nothing here is tied to one machine's setup: the hand-off is purely port-based,
so it works with any local OpenAI-compatible server (llama.cpp / LM Studio /
Ollama) started in any way.

The app's OWN local model (``local_ai``, bundled llama.cpp on its own port) holds
VRAM just the same, so it is stopped and restarted too — through that module, by
recorded model id, so its state file stays truthful.

**Everything here is time-bounded and window-less.** The kill is NEVER gated on
the (potentially slow) WMI command-line capture, so the hand-off cannot hang the
pipeline: worst case we skip the auto-restart (an external watchdog, or the user,
brings the model back). All child processes run hidden (no flashing consoles).
Windows-first; safe no-ops elsewhere.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LOCK_FILE = Path(__file__).resolve().parent.parent / "config" / "GPU_TRANSCRIBE.lock"

_CREATE_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008
_NEW_GROUP = 0x00000200


def _startupinfo():
    """Hidden-window STARTUPINFO on Windows (no console flash); None elsewhere."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def _run(cmd: list[str], timeout: float) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW, startupinfo=_startupinfo()).stdout or ""
    except Exception:          # noqa: BLE001 — best-effort; never break the pipeline
        return ""


def _pids_on_port(port: int, timeout: float = 4.0) -> list[str]:
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                f"-ErrorAction SilentlyContinue).OwningProcess"], timeout)
    return [p.strip() for p in out.split() if p.strip().isdigit()]


def _related_pids_on_port(port: int, timeout: float = 5.0) -> list[str]:
    """Listener PIDs plus stale twins with the exact same command line.

    A crashed/restarted watchdog can leave an older llama-server alive on the
    GPU even though only the newer process owns the port. Killing just the
    listener then makes the TCP check pass while the old model still occupies
    VRAM. Exact command-line equality avoids touching other models or ports.
    """
    port = int(port)
    ps = (
        f"$ids=@((Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique); "
        "if($ids.Count -eq 0){exit}; "
        "$cmds=@(Get-CimInstance Win32_Process | "
        "Where-Object {$ids -contains $_.ProcessId} | "
        "ForEach-Object {$_.CommandLine} | Where-Object {$_}); "
        "Get-CimInstance Win32_Process | "
        "Where-Object {$cmds -contains $_.CommandLine} | "
        "ForEach-Object {$_.ProcessId}"
    )
    out = _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        timeout)
    return sorted({p.strip() for p in out.split() if p.strip().isdigit()})


def _capture_cmdlines(port: int) -> list[str]:
    """Command lines of the listeners on *port*, in ONE short PowerShell call.
    Best-effort and hard-bounded (≤5s) — WMI can be slow, and this must never
    block the hand-off. Returns [] on timeout/failure (→ no auto-restart)."""
    ps = (f"$ids=(Get-NetTCPConnection -LocalPort {port} -State Listen "
          f"-ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique; "
          f"foreach($id in $ids){{ (Get-CimInstance Win32_Process -Filter "
          f"\"ProcessId=$id\" -ErrorAction SilentlyContinue).CommandLine }}")
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=5.0)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _kill_listeners(port: int) -> list[str]:
    pids = _related_pids_on_port(port) or _pids_on_port(port)
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, timeout=5,
                           creationflags=_CREATE_NO_WINDOW, startupinfo=_startupinfo())
        except Exception:      # noqa: BLE001
            pass
    return pids


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def _ready(port: int, timeout: float = 2.0) -> bool:
    """True only when the local server can actually ANSWER — HTTP 200 on a health
    or models route. A big model (e.g. a 22 GB MoE) opens its port *before* it has
    finished loading and llama.cpp then replies 503 ("loading model"); a raw TCP
    check would call that "up" and the next request would fail instantly. Checking
    for a 200 waits for true readiness. Returns False (not ready) on any error."""
    if not port:
        return False
    for path in ("/health", "/v1/models"):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{int(port)}{path}", timeout=timeout) as r:
                if 200 <= r.getcode() < 300:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def _exe_dir(cmdline: str) -> str | None:
    """Directory of the executable in *cmdline*, used as the working directory on
    respawn. Local model servers are almost always launched from their own folder
    with **relative** model paths (e.g. ``-m models\\foo.gguf``); relaunching from
    a different cwd would make the server fail to find its model and never come
    back up. Returns None if it can't be determined (→ inherit the caller's cwd)."""
    try:
        if cmdline.startswith('"'):
            exe = cmdline[1:cmdline.index('"', 1)]
        else:
            exe = cmdline.split(None, 1)[0]
        d = os.path.dirname(exe)
        return d if d and os.path.isdir(d) else None
    except (ValueError, IndexError, OSError):
        return None


def _spawn(cmdline: str) -> None:
    """Relaunch a captured command line, detached and hidden, from the server's
    own directory so relative model paths still resolve."""
    try:
        subprocess.Popen(cmdline, close_fds=True, cwd=_exe_dir(cmdline),
                         creationflags=_DETACHED | _NEW_GROUP | _CREATE_NO_WINDOW,
                         startupinfo=_startupinfo())
    except Exception:          # noqa: BLE001
        pass


def _local_ai():
    """The app's own bundled-llama.cpp module (sibling file), or None."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import local_ai
        return local_ai
    except Exception:          # noqa: BLE001 — optional component
        return None


def _builtin_stop() -> dict:
    """Stop the app's OWN local model, returning what is needed to restore it.

    The hand-off above is port-based and generic (it serves whatever external
    server the user runs on ``llamaPort``). The model the app itself downloads
    and starts lives on its own port (8081 by default) and would otherwise keep
    holding VRAM through the whole transcription. It is stopped through
    ``local_ai`` rather than by port so that module's state file stays truthful —
    a kill by port would leave it believing the server is still running.

    Returns ``{}`` when there is nothing of ours running.
    """
    mod = _local_ai()
    if mod is None:
        return {}
    try:
        st = mod.status()
        if not st.get("running"):
            return {}
        info = {"port": int(st.get("port") or mod.DEFAULT_PORT),
                "model_id": st.get("model_id") or ""}
        mod.stop(info["port"])
        return info
    except Exception:          # noqa: BLE001 — never break the hand-off
        return {}


def _builtin_restart(info: dict, wait: float) -> bool:
    """Bring the app's own local model back. True if it is listening afterwards."""
    if not info:
        return True
    port = int(info.get("port") or 0)
    model_id = info.get("model_id") or ""
    if port and _port_listening(port):
        return True
    if not model_id:            # stopped, but nothing recorded to start again
        return False
    mod = _local_ai()
    if mod is None:
        return False
    try:
        mod.start(model_id, port=port or mod.DEFAULT_PORT, wait=wait)
        return True
    except Exception as exc:    # noqa: BLE001
        print(f"[gpu_handoff] WARNING: could not restart the built-in local model "
              f"{model_id!r} on port {port}: {exc}", file=sys.stderr, flush=True)
        return False


def acquire(port: int = 8080, settle: float = 3.0) -> bool:
    """Take the GPU. ``True`` only if a local model really stopped and must later
    be restored; see :func:`acquire_status` for WHY it is False."""
    return acquire_status(port, settle) == "freed"


def acquire_status(port: int = 8080, settle: float = 3.0) -> str:
    """Take the GPU: (best-effort, bounded) record the LLM command line, drop the
    lock, stop the LLM, let VRAM free. The kill always runs, even if the capture
    times out — so this never hangs.

    Returns one of:

    ``"freed"``  a local model was running and is now stopped - VRAM was released
                 and the caller MUST restore it afterwards;
    ``"idle"``   no local model was running, so there was nothing to unload. This
                 is a NON-EVENT, not a failure: reporting it as one told users
                 their hand-off had broken on every run where they used a cloud
                 provider or simply had the server down;
    ``"stuck"``  something was listening and survived the kill - VRAM was NOT
                 freed, and this is the only real failure.

    The caller treats only ``"freed"`` as "held"; there is nothing to restore in
    the other two cases."""
    # Our own model first: it has its own port, so stopping it here also settles
    # the case where the user pointed ``llamaPort`` at the built-in server.
    builtin = _builtin_stop()
    external = _port_listening(port)
    # Nothing of either kind listening means there is no model to stop or later
    # restore. The old code created a lock and returned True merely because the
    # port was already free; local-provider jobs then waited up to three minutes
    # for a non-existent server to "come back".
    if not external and not builtin:
        return "idle"

    cmds = _capture_cmdlines(port) if external else []   # ≤5s, best-effort
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({"ts": time.time(),
                                     "port": port if external else 0,
                                     "cmds": cmds, "builtin": builtin}),
                         encoding="utf-8")
    if external:
        _kill_listeners(port)          # listener + exact-command stale twins
    time.sleep(settle)                 # give the driver a moment to release VRAM
    if external and _port_listening(port):   # didn't die — one more, harder attempt
        _kill_listeners(port)
        time.sleep(2.0)
    stuck = [str(p) for p in
             ([port] if external and _port_listening(port) else [])
             + ([builtin["port"]] if builtin and _port_listening(builtin["port"]) else [])]
    if stuck:
        print(f"[gpu_handoff] WARNING: port(s) {', '.join(stuck)} still listening "
              f"after kill — the local model was NOT stopped; VRAM was NOT freed "
              f"for transcription.", file=sys.stderr, flush=True)
    return "stuck" if stuck else "freed"


def release(grace: float = 180.0) -> bool:
    """Release the GPU and bring the local LLM back. Removes the lock, and if the
    model is not already listening, restarts it from the recorded command line and
    waits (up to *grace* seconds — a large model can take minutes to load) for the
    port to come up.

    Returns ``True`` if the endpoint is listening when we return (restored, or was
    never really down), ``False`` if it stayed dead — so the caller can fail fast
    with a clear error instead of hanging on the next request. Model loading opens
    the port before it can answer, so a ``True`` here means "a server exists", not
    "instantly ready"; the per-request timeout covers the load-then-answer wait."""
    data = {}
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass
    port = int(data.get("port") or 0)
    cmds = data.get("cmds") or []
    # Our own model is restored first: it is started through ``local_ai`` with a
    # recorded model id, so it is deterministic and needs no watchdog window.
    builtin_ok = _builtin_restart(data.get("builtin") or {}, wait=max(30.0, grace * 0.5))
    if not port:
        return builtin_ok      # no external server recorded → only ours mattered
    if _ready(port):
        return builtin_ok      # already answering (watchdog, or never down)
    # Removing the lock signals an external watchdog (e.g. the user's healthcheck)
    # to bring the model back. PREFER it: wait most of the window for the model to
    # become READY (answering HTTP, not just a port that's open mid-load), and only
    # relaunch the server ourselves as a last resort, so we never race a watchdog
    # for the port. The self-restart uses the server's own directory so relative
    # model paths resolve.
    watchdog_deadline = time.time() + grace * 0.7
    while time.time() < watchdog_deadline:
        if _ready(port):
            return builtin_ok
        time.sleep(1.0)
    for cmd in cmds:           # nobody restored it → fall back to doing it ourselves
        _spawn(cmd)
    deadline = time.time() + grace * 0.3
    while time.time() < deadline:
        if _ready(port):
            return builtin_ok
        time.sleep(1.0)
    # HTTP readiness never confirmed. For a server that simply lacks /health and
    # /v1/models, a listening port is the best signal we have — accept it.
    return builtin_ok and _port_listening(port)


def is_locked() -> bool:
    return LOCK_FILE.exists()


if __name__ == "__main__":
    # CLI so an EXTERNAL watchdog (e.g. the user's healthcheck.py) can honour the
    # hand-off: while the app is transcribing it holds config/GPU_TRANSCRIBE.lock and
    # the model MUST stay down (restarting it would fight the app for VRAM → OOM).
    #
    #   python backend/gpu_handoff.py is-locked   # exit 0 = locked (STAND DOWN),
    #                                             # exit 1 = free (safe to restart)
    #
    # Example healthcheck gate (bash):
    #   python backend/gpu_handoff.py is-locked && exit 0   # transcription in progress
    #   # ... otherwise restart the model ...
    #   python backend/gpu_handoff.py is-locked --max-age 900
    # treats a lock older than 900s as stale (app crashed mid-run) → reports free,
    # so a crash can't keep the model down forever.
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "is-locked"
    if cmd == "is-locked":
        locked = is_locked()
        if locked and "--max-age" in sys.argv:
            try:
                max_age = float(sys.argv[sys.argv.index("--max-age") + 1])
                data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
                if time.time() - float(data.get("ts", 0)) > max_age:
                    locked = False   # stale lock → treat as free
            except (ValueError, OSError, IndexError):
                pass
        print("locked" if locked else "free")
        sys.exit(0 if locked else 1)
    print(f"unknown command: {cmd}", file=sys.stderr)
    sys.exit(2)
