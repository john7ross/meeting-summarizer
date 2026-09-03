"""Self-test for the local-agent provider (ai_client.py, provider='agent').

Drives ai_client.py against a STUB agent CLI (a tiny python script), so the
contract is verified without spending quota on Claude Code / Codex:
  * the transcript reaches the agent on STDIN (an 80k-char meeting cannot be an
    argv parameter — this is the whole reason for the stdin design),
  * {prompt} / {prompt_file} / {text_file} placeholders are substituted,
  * stdout becomes the answer,
  * a failing agent surfaces ITS OWN stderr (so a broken agent config is not
    mistaken for our bug),
  * a missing agent and an unconfigured command fail with a clear message.

    backend\\python\\python.exe desktop\\_selftest_agent_provider.py
"""
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import paths                      # noqa: E402

AI_CLIENT = ROOT / "backend" / "ai_client.py"
PY = str(paths.python_executable())

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


tmp = Path(tempfile.mkdtemp())
agent_dirs_before = set(Path(tempfile.gettempdir()).glob("agent_*"))

# -- stub agents -------------------------------------------------------
echo_agent = tmp / "echo_agent.py"          # prints prompt + what it got on stdin
echo_agent.write_text(
    "import os, sys\n"
    "data = sys.stdin.read()\n"
    "print('PROMPT:', sys.argv[1][:40])\n"
    "print('STDIN_CHARS:', len(data))\n"
    "print('HAS_SYSTEM:', 'Составь саммари' in data)\n"
    "print('HAS_MARKER:', 'ZEBRA-MARKER' in data)\n"
    "print('CWD:', os.getcwd())\n"
    "print('UNICODE: Кириллица сохраняется')\n",
    encoding="utf-8")

fail_agent = tmp / "fail_agent.py"          # exits non-zero with a distinctive stderr
fail_agent.write_text(
    "import sys\n"
    "print('boom: unknown variant `default` in config.toml', file=sys.stderr)\n"
    "sys.exit(3)\n",
    encoding="utf-8")

files_agent = tmp / "files_agent.py"        # reads the file placeholders instead of stdin
files_agent.write_text(
    "import sys\n"
    "p = open(sys.argv[1], encoding='utf-8').read()\n"
    "t = open(sys.argv[2], encoding='utf-8').read()\n"
    "print('FILES_OK', len(p) > 0, 'ZEBRA-MARKER' in t)\n",
    encoding="utf-8")

quota_agent = tmp / "quota_agent.py"        # exits 0 but did not produce an answer
quota_agent.write_text(
    "print(\"You've hit your weekly limit - resets tomorrow\")\n",
    encoding="utf-8")

misconfigured_agent = tmp / "misconfigured_agent.py"   # exits 0, prints its own config error
misconfigured_agent.write_text(
    'print("max_tokens exceeds the provider\'s output cap for this model. "\n'
    '      "Lower model.max_tokens in config.yaml.")\n',
    encoding="utf-8")

corrupt_agent = tmp / "corrupt_agent.py"
corrupt_agent.write_text(
    "print('????????????????????????????????')\n",
    encoding="ascii")

saved_file_agent = tmp / "saved_file_agent.py"
saved_file_agent.write_text(
    "import json, pathlib, sys\n"
    "target = pathlib.Path(sys.argv[1]).with_name('protocol.json')\n"
    "target.write_text(json.dumps({'protocolText': 'КИРИЛЛИЦА_JSON'}, "
    "ensure_ascii=False), encoding='utf-8')\n"
    "print(f'Протокол сохранён в `{target}`.')\n",
    encoding="utf-8")

# A transcript far larger than the Windows argv limit (~32k) — proves stdin is used.
transcript = tmp / "transcript.txt"
big = "[00:00:01] Иван: ZEBRA-MARKER обсуждаем запуск.\n" + ("[00:00:02] Мария: строка.\n" * 3000)
transcript.write_text(big, encoding="utf-8")
check("transcript_exceeds_argv_limit", len(big) > 40000, f"{len(big)} chars")


def run_agent(cmd_template, timeout=120, no_chunk=True,
              prompt="Составь саммари встречи строго по тексту."):
    """--no-chunk by default: to prove the WHOLE transcript reaches the agent in
    one pass we must bypass map-reduce (which would deliberately send parts)."""
    argv = [PY, str(AI_CLIENT), "--provider", "agent",
            "--agent-command", cmd_template,
            "--prompt", prompt,
            "--text-file", str(transcript)]
    if no_chunk:
        argv.append("--no-chunk")
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# -- stdin delivery + {prompt} substitution ----------------------------
proc = run_agent(f'"{PY}" "{echo_agent}" {{prompt}}')
out = proc.stdout or ""
check("agent_exit_ok", proc.returncode == 0, (proc.stderr or "")[-160:])
check("prompt_substituted", "PROMPT: Составь саммари" in out, out[:80])
check("transcript_via_stdin", "STDIN_CHARS: 0" not in out and "STDIN_CHARS:" in out,
      next((l for l in out.splitlines() if "STDIN_CHARS" in l), ""))
check("system_prompt_via_stdin", "HAS_SYSTEM: True" in out, out[:160])
check("full_transcript_delivered", "HAS_MARKER: True" in out, out[:120])
check("unicode_stdout_preserved",
      "UNICODE: Кириллица сохраняется" in out, out[:240])
cwd_line = next((line for line in out.splitlines() if line.startswith("CWD:")), "")
check("default_agent_cwd_is_isolated",
      cwd_line and str(ROOT).lower() not in cwd_line.lower()
      and cwd_line.lower().endswith("workspace"), cwd_line)

# -- {prompt_file} / {text_file} placeholders --------------------------
proc = run_agent(f'"{PY}" "{files_agent}" {{prompt_file}} {{text_file}}')
check("file_placeholders", "FILES_OK True True" in (proc.stdout or ""),
      (proc.stdout or proc.stderr or "")[:120])

# Agent CLIs may print a quota error on stdout and exit 0.  It must never be
# accepted and saved as the generated artifact.
proc = run_agent(f'"{PY}" "{quota_agent}"')
combined = (proc.stdout or "") + (proc.stderr or "")
check("quota_stdout_is_error",
      proc.returncode != 0 and "quota/rate limit" in combined.lower(),
      combined[-180:])

# A misconfigured agent CLI reports on stdout and exits 0. Recognise it, or the
# user is told "invalid JSON/schema" and looks for a bug in the wrong place.
proc = run_agent(f'"{PY}" "{misconfigured_agent}"')
combined = (proc.stdout or "") + (proc.stderr or "")
check("agent_config_error_is_named",
      proc.returncode != 0 and "configuration problem" in combined.lower(),
      combined[-200:])
check("agent_config_error_quotes_the_cause",
      "max_tokens" in combined.lower(), combined[-160:])

proc = run_agent(f'"{PY}" "{corrupt_agent}"')
combined = (proc.stdout or "") + (proc.stderr or "")
check("corrupted_encoding_is_error",
      proc.returncode != 0 and "corrupted character encoding" in combined.lower(),
      combined[-180:])

# Tool-capable agents may save a result in their isolated temp workspace and
# print only its path.  Return that file content before deleting the workspace.
proc = run_agent(f'"{PY}" "{saved_file_agent}" {{prompt_file}}')
saved_out = (proc.stdout or "").strip()
try:
    saved_json = __import__("json").loads(saved_out)
except ValueError:
    saved_json = {}
check("agent_written_result_is_returned",
      proc.returncode == 0 and saved_json.get("protocolText") == "КИРИЛЛИЦА_JSON",
      saved_out[:180])

# -- map-reduce chunking also works through an agent -------------------
# Same oversized transcript WITH chunking on: ai_client must split it, call the
# agent per part and combine — i.e. a huge meeting is still processable.
counter = tmp / "counter_agent.py"
calls_file = tmp / "calls.txt"
counter.write_text(
    "import sys\n"
    f"open(r'{calls_file}', 'a', encoding='utf-8').write('x')\n"
    "sys.stdin.read()\n"
    "print('часть обработана')\n",
    encoding="utf-8")
proc = run_agent(f'"{PY}" "{counter}" {{prompt}}', no_chunk=False)
calls = len(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else 0
check("chunked_agent_multiple_calls", calls > 1, f"{calls} agent invocations")
check("chunked_agent_returns_text", proc.returncode == 0 and (proc.stdout or "").strip() != "",
      (proc.stdout or "")[:60])

# -- a failing agent surfaces its own stderr ---------------------------
proc = run_agent(f'"{PY}" "{fail_agent}"')
combined = (proc.stdout or "") + (proc.stderr or "")
check("failure_is_reported", proc.returncode != 0, f"rc={proc.returncode}")
check("agent_stderr_surfaced", "config.toml" in combined, combined[-160:])

# -- missing agent binary ----------------------------------------------
proc = run_agent("definitely-not-a-real-agent-xyz {prompt}")
combined = (proc.stdout or "") + (proc.stderr or "")
check("missing_agent_clear_error",
      ("not found" in combined.lower()) or ("agent" in combined.lower()),
      combined[-160:])

# -- unconfigured command ----------------------------------------------
proc = subprocess.run(
    [PY, str(AI_CLIENT), "--provider", "agent", "--prompt", "x",
     "--text-file", str(transcript)],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
combined = (proc.stdout or "") + (proc.stderr or "")
check("unconfigured_command_error", "not configured" in combined.lower(), combined[-160:])

# -- provider is accepted by the desktop command builder ---------------
from app.backend import summarization as S     # noqa: E402
check("provider_registered", "agent" in S.PROVIDERS, str(S.PROVIDERS))
cmd = S.build_command("p", transcript, provider="agent",
                      agent_command="claude -p {prompt}", agent_cwd=r"C:\tmp")
check("build_command_passes_agent", "--agent-command" in cmd and "--agent-cwd" in cmd)

# -- Windows npm/PowerShell shim ---------------------------------------
if sys.platform == "win32":
    shim = tmp / "stub-agent.ps1"
    shim.write_text(
        "\ufeff$data = [Console]::In.ReadToEnd()\n"
        "Write-Output ('PS_ARGC:' + $args.Count)\n"
        "Write-Output ('PS_PROMPT:' + $args[2])\n"
        "Write-Output ('PS_STDIN:' + $data.Length)\n"
        "Write-Output 'PS_UNICODE:Кириллица сохраняется'\n",
        encoding="utf-8")
    # shutil.which('stub-agent') resolves the .cmd npm-style launcher, while
    # ai_client must deliberately choose the safe sibling .ps1 file.
    (tmp / "stub-agent.cmd").write_text(
        "@echo off\r\n"
        "echo UNSAFE_CMD_SHOULD_NOT_RUN\r\n",
        encoding="ascii")
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(tmp) + os.pathsep + old_path
    dangerous = 'literal & | < > %PATH% ! "quoted"'
    proc = run_agent("stub-agent --skip-trust -p {prompt}", prompt=dangerous)
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("windows_ps1_shim_runs",
          proc.returncode == 0 and "PS_ARGC:3" in combined and "PS_PROMPT:" in combined,
          combined[-180:])
    check("windows_cmd_shim_not_run", "UNSAFE_CMD_SHOULD_NOT_RUN" not in combined,
          combined[-180:])
    check("windows_ps1_unicode_preserved",
          "PS_UNICODE:Кириллица сохраняется" in combined,
          combined[-240:])
    check("windows_prompt_not_shell_executed",
          dangerous in combined and "is not recognized" not in combined.lower()
          and "PS_STDIN:" in combined,
          combined[-180:])
    os.environ["PATH"] = old_path

agent_dirs_after = set(Path(tempfile.gettempdir()).glob("agent_*"))
check("agent_temp_files_cleaned", not (agent_dirs_after - agent_dirs_before),
      ", ".join(str(p) for p in sorted(agent_dirs_after - agent_dirs_before)))

# An agent CLI spawns children whose handles on the scratch directory can outlive
# the call on Windows. Cleanup then raises WinError 32 from a `finally` block and
# DESTROYS the answer that was already produced - seen live as "анализ выполнен
# не полностью: ошибок 2 из 11" on a run whose text was fine.
sys.path.insert(0, str(ROOT / "backend"))
import ai_client as _ac                                    # noqa: E402


class _StubbornTempDir:
    """Refuses to be removed, the way a locked Windows directory does."""

    def __init__(self, fail_times):
        self.remaining = fail_times
        self.calls = 0

    def cleanup(self):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise PermissionError(
                32, "The process cannot access the file because it is being "
                    "used by another process")


locked = _StubbornTempDir(fail_times=99)
try:
    _ac._release_temp_dir(locked, attempts=3, delay=0)
    check("locked_workspace_never_raises", True, f"{locked.calls} attempts, gave up quietly")
except Exception as exc:                                    # noqa: BLE001
    check("locked_workspace_never_raises", False, repr(exc))

transient = _StubbornTempDir(fail_times=2)
_ac._release_temp_dir(transient, attempts=5, delay=0)
check("transient_lock_is_retried", transient.calls == 3,
      f"cleaned on attempt {transient.calls}")

clean = _StubbornTempDir(fail_times=0)
_ac._release_temp_dir(clean, attempts=5, delay=0)
check("unlocked_workspace_removed_at_once", clean.calls == 1, f"{clean.calls} call")

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if failed else 0)
