"""Keep the app's local model up — and stand down while the GPU is handed off.

``gpu_handoff`` stops the local model before GPU transcription and restarts it
afterwards, but that only covers the runs the app itself completes. The model
still ends up down after a crash mid-transcription, an out-of-memory kill, a
driver reset or a reboot — and then every "local" summary fails until someone
notices. This is the watchdog that closes that gap:

  * while ``config/GPU_TRANSCRIBE.lock`` exists it does NOTHING — restarting the
    model during transcription would fight it for VRAM and OOM both;
  * a lock older than ``--max-age`` (default 15 min) is treated as stale, i.e.
    the app died holding it, so a crash cannot keep the model down forever;
  * otherwise, if the model is not answering, it starts it again.

Run it either way::

    python backend\\local_ai_watchdog.py --model qwen3-8b-q4        # loop forever
    python backend\\local_ai_watchdog.py --once                     # one check

``--once`` is the form to put in Task Scheduler / cron; the looping form is for
running it next to the app. Both are stdlib-only and print one line per action,
so the output is readable in a log.

An external server (your own llama.cpp / LM Studio / Ollama started outside the
app) is not this module's business: point ``--exec`` at its command line if you
want it supervised too.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gpu_handoff                                   # noqa: E402
import local_ai                                      # noqa: E402


def _handoff_in_progress(max_age: float) -> bool:
    """True while the app holds the GPU. A lock older than *max_age* seconds is
    stale (the app crashed mid-run) and is ignored, otherwise one crash would
    keep the model down until a human removed the file."""
    if not gpu_handoff.is_locked():
        return False
    try:
        data = json.loads(gpu_handoff.LOCK_FILE.read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts", 0)) > max_age:
            return False
    except (OSError, ValueError, TypeError):
        pass
    return True


def _wanted_model(explicit: str, port: int) -> str:
    """Which model to bring up: the one asked for, else the one last started."""
    if explicit:
        return explicit
    return local_ai.status(port).get("model_id") or ""


def check_once(model: str = "", port: int = 0, max_age: float = 900.0,
               exec_cmd: str = "", start_wait: float = 180.0) -> str:
    """One supervision tick. Returns a one-line human-readable outcome."""
    port = int(port or local_ai.DEFAULT_PORT)
    if _handoff_in_progress(max_age):
        return "standing down: transcription holds the GPU"
    if gpu_handoff._ready(port):
        return f"ok: local model answering on {port}"

    if exec_cmd:
        gpu_handoff._spawn(exec_cmd)
        return f"restarted external server on {port}: {exec_cmd[:60]}"

    wanted = _wanted_model(model, port)
    if not wanted:
        # ASCII only: this goes to a console that is not always UTF-8.
        return "cannot restart: no model recorded and none given - pass --model <id>"
    try:
        local_ai.start(wanted, port=port, wait=start_wait)
        return f"restarted {wanted} on {port}"
    except Exception as exc:                         # noqa: BLE001
        return f"restart of {wanted} failed: {exc}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="",
                    help="model id to keep up (default: the last one started)")
    ap.add_argument("--port", type=int, default=local_ai.DEFAULT_PORT)
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between checks in loop mode")
    ap.add_argument("--max-age", type=float, default=900.0,
                    help="a hand-off lock older than this is treated as stale")
    ap.add_argument("--exec", dest="exec_cmd", default="",
                    help="supervise this external command line instead of the "
                         "app's own model")
    ap.add_argument("--once", action="store_true",
                    help="run a single check and exit (for Task Scheduler/cron)")
    args = ap.parse_args(argv)

    while True:
        line = check_once(args.model, args.port, args.max_age, args.exec_cmd)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}", flush=True)
        if args.once:
            return 0 if not line.startswith(("cannot restart", "restart of")) else 1
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
