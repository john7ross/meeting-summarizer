"""Worker/queue self-test: 4 concurrent jobs, concurrency limit 2.

Proves: (1) the concurrency cap holds, (2) every job runs to completion,
(3) a failing job reports failure with its own error, (4) NO cross-talk —
each job's progress details carry only its own label. Runs a real Qt event
loop headlessly (QtCore only, no display).
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from app import paths  # noqa: E402
from app.core.queue_manager import QueueManager, resolve_workers  # noqa: E402
from app.core.worker import concise_process_error, utf8_process_environment  # noqa: E402
from app.backend import transcription as T  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


FAKE = str(Path(__file__).resolve().parent / "_fake_processor.py")
PY = str(paths.python_executable())

app = QCoreApplication.instance() or QCoreApplication(sys.argv)
manager = QueueManager(max_concurrency=2)

progress_by_job: dict[int, list] = {}
result_by_job: dict[int, object] = {}
max_active = {"v": 0}

manager.active_changed.connect(lambda n: max_active.update(v=max(max_active["v"], n)))
manager.job_progress.connect(
    lambda jid, ev: progress_by_job.setdefault(jid, []).append(ev.details))
manager.job_done.connect(lambda jid, res: result_by_job.__setitem__(jid, res))
manager.all_done.connect(lambda: QTimer.singleShot(0, app.quit))

JOBS = [1001, 1002, 1003, 1004]
for jid in JOBS:
    cmd = [PY, FAKE, "--label", f"L{jid}", "--steps", "4", "--delay", "0.05"]
    if jid == 1003:
        cmd.append("--fail")
    manager.enqueue(jid, cmd)

QTimer.singleShot(30000, app.quit)  # safety timeout

try:
    app.exec()

    check("all_jobs_completed", set(result_by_job) == set(JOBS),
          f"{sorted(result_by_job)}")
    check("concurrency_cap_held", max_active["v"] == 2, f"peak active={max_active['v']}")

    ok_success = all(
        result_by_job[j].success for j in (1001, 1002, 1004)
        if j in result_by_job)
    check("successes_ok", ok_success)

    failed = result_by_job.get(1003)
    check("failure_reported", failed is not None and not failed.success
          and "L1003" in (failed.error or ""), f"{getattr(failed, 'error', None)!r}")

    no_crosstalk = all(
        set(details) == {f"L{jid}"} for jid, details in progress_by_job.items())
    check("no_crosstalk", no_crosstalk,
          "each job saw only its own label" if no_crosstalk
          else f"{ {k: set(v) for k, v in progress_by_job.items()} }")

    check("resolve_workers_auto_cpu", resolve_workers("auto", cuda=False) >= 1)
    check("resolve_workers_cuda_conservative", resolve_workers("auto", cuda=True) == 1)
    check("resolve_workers_explicit", resolve_workers("6") == 6 and resolve_workers(8) == 8)
    utf8_env = utf8_process_environment()
    check("subprocess_io_forced_utf8",
          utf8_env.value("PYTHONUTF8") == "1"
          and utf8_env.value("PYTHONIOENCODING") == "utf-8")

    noisy_quota = (
        "\x1b[31mError: Agent failed: your current usage\\n' +\n"
        "* Quota exceeded for metric: requests, limit: 20, model: gemini-3-flash\\n' +\n"
        "Please retry in 46.3s.',\n"
        "details: [ [Object] ]\n}\n"
        "An unexpected critical error occurred:[object Object]\n"
        "Traceback: Traceback (most recent call last):\n  File \"ai_client.py\"")
    concise = concise_process_error(noisy_quota)
    check("agent_error_is_concise",
          "quota exceeded" in concise.lower()
          and "Please retry in 46.3s." in concise
          and "Traceback" not in concise
          and "[Object]" not in concise
          and len(concise) <= 600,
          concise)

    trusted = concise_process_error(
        "Error: Agent failed: Gemini CLI is not running in a trusted directory.\n"
        "Traceback: Traceback (most recent call last):")
    check("normal_agent_error_preserved",
          trusted == "Gemini CLI is not running in a trusted directory.", trusted)
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
