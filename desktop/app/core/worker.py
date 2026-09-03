"""One transcription worker per active job, driven by QProcess.

Event-driven (no threads): QProcess streams the backend's stdout, which we
parse into ProgressEvent / ResultEvent and re-emit as Qt signals tagged with
this worker's ``job_id``. Each worker owns exactly one job, so events can never
be attributed to the wrong file — the id is carried end to end.
"""
from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Signal

from ..backend import transcription as T


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_PREFIX = re.compile(r"^(?:Error:\s*)?(?:Agent failed:\s*)?", re.I)


def utf8_process_environment() -> QProcessEnvironment:
    """System environment with deterministic UTF-8 Python subprocess I/O."""
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUTF8", "1")
    env.insert("PYTHONIOENCODING", "utf-8")
    return env


def concise_process_error(raw: str, limit: int = 600) -> str:
    """Turn noisy CLI stderr into a bounded, user-facing explanation.

    Agent CLIs sometimes append JavaScript objects, ANSI colour codes and the
    complete ``ai_client.py`` traceback to the useful provider message.  The
    full stderr remains available to the subprocess itself; the desktop must
    not stretch the queue/status UI with several screens of implementation
    detail.
    """
    text = _ANSI_ESCAPE.sub("", raw or "").strip()
    if not text:
        return ""
    # ai_client prefixes its Python traceback after the actual provider error.
    text = re.split(r"\r?\nTraceback:\s*", text, maxsplit=1)[0]
    # Gemini CLI renders embedded newlines as JS string fragments.
    text = text.replace("\\n", "\n")
    useful: list[str] = []
    for source_line in text.splitlines():
        line = source_line.strip().strip("'\",")
        line = re.sub(r"^\+\s*", "", line).strip().strip("'\",")
        if not line:
            continue
        low = line.lower()
        if (low.startswith(("details:", "retrydelayms:", "reason:"))
                or line in {"}", "{", "[Object]", "[object Object]"}
                or "unexpected critical error occurred:[object object]" in low):
            continue
        line = _ERROR_PREFIX.sub("", line).strip()
        if line and line not in useful:
            useful.append(line)

    if not useful:
        useful = ["AI process failed"]

    joined = " ".join(useful)
    quota = re.search(
        r"quota exceeded.*?(?=(?:please retry|$))", joined, flags=re.I)
    retry = re.search(r"please retry in\s+[\d.]+\s*s", joined, flags=re.I)
    if quota:
        message = "AI provider quota exceeded: " + quota.group(0).strip(" ,.'")
        if retry:
            message += ". " + retry.group(0).rstrip(".") + "."
    else:
        message = joined

    message = re.sub(r"\s+", " ", message).strip()
    if len(message) > limit:
        message = message[:max(0, limit - 1)].rstrip() + "…"
    return message


class FnWorker(QThread):
    """Run a plain (non-Qt) callable off the UI thread and signal when done.

    Used for the GPU hand-off (stopping/restarting the local LLM), which shells
    out to PowerShell and sleeps — work that must NOT block the UI thread. The
    ``done`` signal is delivered back on the main thread, so its slot may safely
    touch Qt objects (e.g. launch the next QProcess)."""
    done = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self._fn()
        except Exception:      # noqa: BLE001 — best-effort; report via done regardless
            pass
        finally:
            # MUST always fire: the pipeline resumes (launches transcription /
            # starts the summary) from this signal. Missing it strands the job.
            self.done.emit()


class TranscriptionWorker(QObject):
    # job_id, ProgressEvent
    progress = Signal(object, object)
    # job_id, ResultEvent (emitted exactly once, success or failure)
    done = Signal(object, object)

    def __init__(self, job_id: int, command, cwd=None, parent=None):
        super().__init__(parent)
        self.job_id = int(job_id)
        self._environment = dict(getattr(command, "environment", {}))
        self._command = [str(part) for part in command]
        self._cwd = str(cwd) if cwd else None
        self._proc: Optional[QProcess] = None
        self._buffer = ""
        self._result = None
        self._done_emitted = False

    def start(self) -> None:
        proc = QProcess(self)
        if self._cwd:
            proc.setWorkingDirectory(self._cwd)
        env = utf8_process_environment()
        for key, value in self._environment.items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.setProgram(self._command[0])
        proc.setArguments(self._command[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()

    def stop(self) -> None:
        if self._proc and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    # -- internal ------------------------------------------------------
    def _consume(self, line: str) -> None:
        event = T.parse_event(line)
        if event is None:
            return
        if isinstance(event, T.ResultEvent):
            self._result = event
        else:
            self.progress.emit(self.job_id, event)

    def _on_stdout(self) -> None:
        if not self._proc:
            return
        chunk = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._consume(line)

    def _on_error(self, error) -> None:
        # If the process never started, `finished` won't fire — finalize here.
        if error == QProcess.ProcessError.FailedToStart:
            self._emit_done(-1)

    def _on_finished(self, exit_code: int, _exit_status=None) -> None:
        if self._buffer.strip():
            self._consume(self._buffer)
            self._buffer = ""
        self._emit_done(exit_code)

    def _emit_done(self, exit_code: int) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        result = self._result
        if result is None:
            stderr = ""
            if self._proc:
                stderr = bytes(self._proc.readAllStandardError()).decode(
                    "utf-8", "replace").strip()
            result = T.ResultEvent(
                success=(exit_code == 0),
                error=None if exit_code == 0 else (stderr or f"exit code {exit_code}"),
            )
        self.done.emit(self.job_id, result)


class AiWorker(QObject):
    """Runs one ai_client.py pass (summary or analysis) and captures its text.

    ai_client prints the generated text to stdout and uses a non-zero exit code
    on failure. Output is plain text (not the progress protocol), so this worker
    just accumulates stdout and reports it once the process finishes.
    """

    # job_id, ok, text, error
    done = Signal(object, bool, str, str)

    def __init__(self, job_id: int, command, cwd=None, parent=None):
        super().__init__(parent)
        self.job_id = int(job_id)
        self._environment = dict(getattr(command, "environment", {}))
        self._command = [str(part) for part in command]
        self._cwd = str(cwd) if cwd else None
        self._proc: Optional[QProcess] = None
        self._out = b""
        self._err = b""
        self._done_emitted = False

    def start(self) -> None:
        proc = QProcess(self)
        if self._cwd:
            proc.setWorkingDirectory(self._cwd)
        env = utf8_process_environment()
        for key, value in self._environment.items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.setProgram(self._command[0])
        proc.setArguments(self._command[1:])
        proc.readyReadStandardOutput.connect(self._read_out)
        proc.readyReadStandardError.connect(self._read_err)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()

    def stop(self) -> None:
        if self._proc and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _read_out(self) -> None:
        if self._proc:
            self._out += bytes(self._proc.readAllStandardOutput())

    def _read_err(self) -> None:
        if self._proc:
            self._err += bytes(self._proc.readAllStandardError())

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._emit(-1)

    def _on_finished(self, exit_code: int, _exit_status=None) -> None:
        self._read_out()
        self._read_err()
        self._emit(exit_code)

    def _emit(self, exit_code: int) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        text = self._out.decode("utf-8", "replace").strip()
        error = concise_process_error(
            self._err.decode("utf-8", "replace").strip())
        ok = (exit_code == 0 and bool(text))
        if not ok and not error:
            error = text or f"exit code {exit_code}"
        self.done.emit(self.job_id, ok, text, error)


class ExportWorker(QThread):
    """Runs one blocking ``exporter.export()`` call off the UI thread.

    reportlab / python-docx are synchronous CPU work, so the export runs in a
    QThread and reports the written path (or an error message) exactly once.
    """

    # ok, path, error
    done = Signal(bool, str, str)

    def __init__(self, kind, data, fmt, out_path, meta, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._data = data
        self._fmt = fmt
        self._out_path = str(out_path)
        self._meta = dict(meta or {})

    def run(self) -> None:
        try:
            from ..backend import exporter
            path = exporter.export(self._kind, self._data, self._fmt,
                                   self._out_path, self._meta)
            self.done.emit(True, str(path), "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, "", str(exc))


class ObsidianWorker(QThread):
    """Runs one blocking ``obsidian.export_to_obsidian()`` off the UI thread."""

    # ok, summary_note_path, error
    done = Signal(bool, str, str)

    def __init__(self, vault, kwargs, parent=None):
        super().__init__(parent)
        self._vault = vault
        self._kwargs = dict(kwargs)

    def run(self) -> None:
        try:
            from ..backend import obsidian
            res = obsidian.export_to_obsidian(self._vault, **self._kwargs)
            # Report the note that was actually written. Reading only "summary"
            # meant that exporting an analysis or a transcript emitted an empty
            # path, and the UI showed no result at all - the button looked dead.
            written = (res.get("summary") or res.get("analysis")
                       or res.get("transcript") or "")
            self.done.emit(bool(written), written,
                           "" if written else "nothing was written to the vault")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, "", str(exc))


class RagWorker(QThread):
    """Runs one ``rag.py`` subcommand off the UI thread and parses its JSON.

    rag.py prints a single JSON object to stdout on success and a JSON error to
    stderr on failure (non-zero exit). This worker runs the bundled python so
    embeddings/chromadb come from the same environment as the rest of the
    backend, captures both streams, and emits the parsed result exactly once.
    """

    # op, ok, result_dict, error
    done = Signal(str, bool, object, str)

    def __init__(self, op: str, command, parent=None):
        super().__init__(parent)
        self._op = op
        self._command = [str(part) for part in command]

    def run(self) -> None:
        import json
        import subprocess
        try:
            proc = subprocess.run(
                self._command, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(self._op, False, None, str(exc))
            return
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0 and out:
            try:
                data = json.loads(out.splitlines()[-1])
                self.done.emit(self._op, bool(data.get("success", True)),
                               data, "" if data.get("success", True)
                               else data.get("error", ""))
                return
            except ValueError:
                self.done.emit(self._op, False, None,
                               f"could not parse rag output: {out[:300]}")
                return
        # failure path: prefer a JSON error message from stderr
        msg = err or out or f"exit code {proc.returncode}"
        try:
            data = json.loads((err or out).splitlines()[-1])
            msg = data.get("error", msg)
        except (ValueError, IndexError):
            pass
        self.done.emit(self._op, False, None, msg)


class DeviceWorker(QThread):
    """Probes CUDA availability in a subprocess (torch import is slow) and reports
    it exactly once, so the UI never blocks on startup (TODO #9)."""

    detected = Signal(bool, str)   # cuda, device_name ("" when CPU)

    def __init__(self, python_exe, parent=None):
        super().__init__(parent)
        self._python = str(python_exe)

    def run(self) -> None:
        from .device import probe
        info = probe(self._python)
        self.detected.emit(bool(info.get("cuda")), info.get("name") or "")


class CompareWorker(QThread):
    """Engine A/B (TODO #10): run the REAL processor once per (engine, model) over
    ONE audio file, measuring wall time and collecting each transcript, so the user
    can compare the free local engines on speed + output. Engines run sequentially;
    a failing engine (missing model/dep) is reported and does not abort the rest."""

    engine_done = Signal(str, object)   # engine_id, result dict
    finished_all = Signal(object)       # list[result]

    def __init__(self, python_exe, processor_script, video, specs, language,
                 device, out_root, parent=None):
        super().__init__(parent)
        self._python = str(python_exe)
        self._script = str(processor_script)
        self._video = str(video)
        self._specs = list(specs)   # [(engine_id, model)]
        self._language = language
        self._device = device
        self._out_root = str(out_root)

    def run(self) -> None:
        import json
        import os
        import subprocess
        import time
        results = []
        for engine, model in self._specs:
            outdir = os.path.join(self._out_root, engine)
            os.makedirs(outdir, exist_ok=True)
            cmd = [self._python, self._script, "--video", self._video,
                   "--language", self._language, "--model", str(model),
                   "--engine", engine, "--device", self._device,
                   "--output-dir", outdir]
            t0 = time.time()
            text, err, ok = "", "", False
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
                secs = time.time() - t0
                out_path = ""
                for line in reversed((proc.stdout or "").strip().splitlines()):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if "success" in obj:
                        ok = bool(obj.get("success"))
                        out_path = obj.get("output", "")
                        err = obj.get("error", "") or ""
                        break
                if ok and out_path and os.path.exists(out_path):
                    text = open(out_path, encoding="utf-8", errors="replace").read()
                elif not err:
                    err = (proc.stderr or "").strip()[-300:] or f"exit {proc.returncode}"
            except Exception as exc:   # noqa: BLE001
                secs = time.time() - t0
                err = str(exc)[:300]
            res = {"engine": engine, "model": model, "seconds": round(secs, 1),
                   "chars": len(text), "text": text, "ok": bool(ok and text),
                   "error": "" if (ok and text) else err}
            results.append(res)
            self.engine_done.emit(engine, res)
        self.finished_all.emit(results)


class ModelsWorker(QObject):
    """Drives one ``models_cli.py`` op (download or check-update) via QProcess
    and streams its JSON-lines output as Qt signals.

    download:    a ``progress(percent, detail)`` per progress line, then
                 ``done(ok, result, error)`` once.
    check-update: a single terminal JSON object -> ``done(ok, result, error)``.
    """

    progress = Signal(int, str)
    done = Signal(bool, object, str)   # ok, result_dict|None, error

    def __init__(self, command, parent=None):
        super().__init__(parent)
        self._command = [str(part) for part in command]
        self._proc: Optional[QProcess] = None
        self._buffer = ""
        self._result = None
        self._error = ""
        self._ok = None
        self._done_emitted = False

    def start(self) -> None:
        proc = QProcess(self)
        proc.setProcessEnvironment(utf8_process_environment())
        proc.setProgram(self._command[0])
        proc.setArguments(self._command[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()

    def stop(self) -> None:
        if self._proc and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _consume(self, line: str) -> None:
        import json
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except ValueError:
            return
        event = obj.get("event")
        if event == "progress":
            self.progress.emit(int(obj.get("percent", 0)), str(obj.get("detail", "")))
        elif event == "done":
            self._ok, self._result = True, obj
        elif event == "error":
            self._ok, self._error = False, str(obj.get("error", "error"))
        else:  # single-shot JSON (check-update) — terminal result
            self._ok = bool(obj.get("ok", True))
            self._result = obj
            if not self._ok:
                self._error = str(obj.get("error", ""))

    def _on_stdout(self) -> None:
        if not self._proc:
            return
        chunk = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._consume(line)

    def _on_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._emit_done(-1)

    def _on_finished(self, exit_code: int, _status=None) -> None:
        if self._buffer.strip():
            self._consume(self._buffer)
            self._buffer = ""
        self._emit_done(exit_code)

    def _emit_done(self, exit_code: int) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        if self._ok is None:
            stderr = ""
            if self._proc:
                stderr = bytes(self._proc.readAllStandardError()).decode(
                    "utf-8", "replace").strip()
            self._ok = (exit_code == 0)
            if not self._ok and not self._error:
                self._error = stderr or f"exit code {exit_code}"
        self.done.emit(bool(self._ok), self._result, self._error)
