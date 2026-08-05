"""Diagnostics window (TODO #10) — REAL observability for a heavy desktop app.

Four tabs, all on real data (no Electron-era stubs):
  * System   — live CPU / RAM / GPU (core/metrics.py: psutil + nvidia-smi)
  * Timeline — a processed meeting's performance trace (core/trace.py rendering
               the backend's real <id>/<stem>_trace.json spans)
  * Logs     — tail of logs/app.log (logging_setup.py)

Deliberately OMITTED from the old Electron suite because they do not apply to the
PySide/QProcess architecture (they would be stubs): Electron IPC metrics, the JS
eval console, watched variables / call stack / heap snapshots, and the JS-side
A/B / regression / coverage harness. Engine A/B is reimagined as its own real
feature (compare_dialog.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QFrame, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import theme
from .. import paths
from ..core import metrics, trace
from ..core.models import JobStatus, main_label
from ..core.worker import CompareWorker
from .flamegraph import FlameGraphWidget
from ..logging_setup import log_path

_L = {
    "ru": {
        "title": "Диагностика", "tab_sys": "Система", "tab_time": "Профиль обработки",
        "tab_logs": "Логи", "cpu": "CPU", "ram": "ОЗУ", "gpu": "GPU",
        "no_gpu": "GPU не обнаружен (нет nvidia-smi)", "no_psutil": "psutil недоступен",
        "pick": "Встреча:", "no_trace": "Нет данных профиля для этой встречи.",
        "total": "Итого", "refresh": "Обновить", "no_logs": "Лог пуст.",
        "no_meetings": "Нет обработанных встреч.",
        "tab_cmp": "Сравнение движков", "cmp_file": "Файл:", "cmp_browse": "Обзор…",
        "cmp_lang": "Язык:", "cmp_run": "Запустить сравнение", "cmp_running": "Идёт сравнение…",
        "cmp_pick_file": "Выберите аудио/видео файл.", "cmp_pick_engines": "Отметьте хотя бы один движок.",
        "cmp_engine": "Движок", "cmp_model": "Модель", "cmp_time": "Время, с",
        "cmp_chars": "Символов", "cmp_status": "Статус", "cmp_ok": "✓", "cmp_missing": "нет модели",
    },
    "en": {
        "title": "Diagnostics", "tab_sys": "System", "tab_time": "Processing profile",
        "tab_logs": "Logs", "cpu": "CPU", "ram": "RAM", "gpu": "GPU",
        "no_gpu": "No GPU detected (no nvidia-smi)", "no_psutil": "psutil unavailable",
        "pick": "Meeting:", "no_trace": "No profile data for this meeting.",
        "total": "Total", "refresh": "Refresh", "no_logs": "Log is empty.",
        "no_meetings": "No processed meetings yet.",
        "tab_cmp": "Compare engines", "cmp_file": "File:", "cmp_browse": "Browse…",
        "cmp_lang": "Language:", "cmp_run": "Run comparison", "cmp_running": "Comparing…",
        "cmp_pick_file": "Choose an audio/video file.", "cmp_pick_engines": "Tick at least one engine.",
        "cmp_engine": "Engine", "cmp_model": "Model", "cmp_time": "Time, s",
        "cmp_chars": "Chars", "cmp_status": "Status", "cmp_ok": "✓", "cmp_missing": "no model",
    },
}


def _fetch_catalog(python_exe, script) -> dict:
    """models_cli.py catalog (engines + models + availability). {} on failure."""
    try:
        env = dict(os.environ); env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"
        out = subprocess.run([str(python_exe), str(script), "catalog"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30, env=env)
        return json.loads((out.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"engines": []}


def _t(key, lang):
    return _L.get(lang, _L["ru"]).get(key, key)


def _tail(path: str, max_lines: int = 500) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


class DiagnosticsDialog(QDialog):
    def __init__(self, store, language: str = "ru", parent=None,
                 python_exe=None, models_cli_script=None, processor_script=None,
                 catalog=None):
        super().__init__(parent)
        self._store = store
        self._lang = language
        self._python = str(python_exe or paths.python_executable())
        self._models_cli = str(models_cli_script or paths.MODELS_CLI_SCRIPT)
        self._processor = str(processor_script or paths.PROCESSOR_SCRIPT)
        self._catalog = catalog if catalog is not None else _fetch_catalog(
            self._python, self._models_cli)
        self._cmp_worker = None
        self.setWindowTitle(_t("title", language))
        self.setMinimumSize(680, 520)
        self.resize(860, 640)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tabs.addTab(self._build_system_tab(), _t("tab_sys", self._lang))
        self.tabs.addTab(self._build_timeline_tab(), _t("tab_time", self._lang))
        self.tabs.addTab(self._build_compare_tab(), _t("tab_cmp", self._lang))
        self.tabs.addTab(self._build_logs_tab(), _t("tab_logs", self._lang))

        # live system sampling while the dialog is open
        if metrics.psutil:
            metrics.sample()  # prime cpu_percent so the first shown value is real
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._refresh_system)
        self._timer.start()
        self._refresh_system()
        self._refresh_logs()
        self._reload_meetings()

    # -- System --------------------------------------------------------
    def _build_system_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self._bars = {}
        self._vals = {}
        for key in ("cpu", "ram", "gpu"):
            row = QHBoxLayout()
            lbl = QLabel(_t(key, self._lang))
            lbl.setMinimumWidth(48)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setObjectName("diagBar")
            val = QLabel("—")
            val.setMinimumWidth(220)
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            row.addWidget(val)
            v.addLayout(row)
            self._bars[key] = bar
            self._vals[key] = val
        self._sys_note = QLabel("")
        self._sys_note.setObjectName("hint")
        v.addWidget(self._sys_note)
        v.addStretch(1)
        return w

    def _refresh_system(self) -> None:
        s = metrics.sample()
        if not s.get("psutil"):
            self._sys_note.setText(_t("no_psutil", self._lang))
        cpu = s.get("cpu_percent")
        self._bars["cpu"].setValue(int(cpu or 0))
        self._vals["cpu"].setText(f"{cpu:.0f}%" if cpu is not None else "—")
        rp = s.get("ram_percent")
        self._bars["ram"].setValue(int(rp or 0))
        if rp is not None:
            self._vals["ram"].setText(
                f"{rp:.0f}%  ·  {s['ram_used_mb'] / 1024:.1f}/{s['ram_total_mb'] / 1024:.1f} GB")
        gpu = s.get("gpu")
        if gpu:
            self._bars["gpu"].setValue(int(gpu.get("util", 0)))
            self._vals["gpu"].setText(
                f"{gpu.get('util', 0)}%  ·  {gpu['mem_used_mb'] / 1024:.1f}/"
                f"{gpu['mem_total_mb'] / 1024:.1f} GB  ·  {gpu.get('name', '')}")
        else:
            self._bars["gpu"].setValue(0)
            self._vals["gpu"].setText(_t("no_gpu", self._lang))

    # -- Timeline ------------------------------------------------------
    def _build_timeline_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel(_t("pick", self._lang)))
        self.cb_meeting = QComboBox()
        # Entries are meeting file names: without a cap the widest one
        # stretched the whole dialog past 2300 px.
        theme.cap_combo_width(self.cb_meeting)
        self.cb_meeting.currentIndexChanged.connect(self._render_trace)
        top.addWidget(self.cb_meeting, 1)
        v.addLayout(top)
        self.trace_header = QLabel("")
        self.trace_header.setObjectName("hint")
        v.addWidget(self.trace_header)
        self.flame = FlameGraphWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.flame)
        v.addWidget(scroll, 1)
        return w

    def _reload_meetings(self) -> None:
        self.cb_meeting.blockSignals(True)
        self.cb_meeting.clear()
        entries = []
        try:
            entries = list(self._store.load())
        except Exception:
            entries = []
        # The same recording is often processed repeatedly.  A bare video name
        # made those runs indistinguishable and could make a failed run look as
        # if the completed run had no profile.  Show newest runs first and keep
        # the artifact folder/status visible in the chooser.
        for e in reversed(entries):
            try:
                status = main_label(JobStatus(e.status), self._lang)
            except (TypeError, ValueError):
                status = str(e.status or "")
            name = e.video_name or str(e.id)
            folder = e.folder or str(e.id)
            self.cb_meeting.addItem(f"{name} · {status} · {folder}", e.id)
        self.cb_meeting.blockSignals(False)
        if self.cb_meeting.count():
            self._render_trace()
        else:
            self.trace_header.setText(_t("no_meetings", self._lang))
            self.flame.set_layout(None)

    def _render_trace(self, *_) -> None:
        eid = self.cb_meeting.currentData()
        if eid is None:
            return
        tpath = trace.find_trace(self._store.job_dir(eid))
        data = trace.load_trace(tpath) if tpath else None
        if not data or not data.get("spans"):
            self.trace_header.setText(_t("no_trace", self._lang))
            self.flame.set_layout(None)
            return
        lay = trace.layout(data)
        self.trace_header.setText(
            f"{lay['name']} · {_t('total', self._lang)}: "
            f"{lay['total_ms'] / 1000:.1f} s · {lay['timestamp']}")
        self.flame.set_layout(lay)

    # -- Logs ----------------------------------------------------------
    def _build_logs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("diagLogs")
        v.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton(_t("refresh", self._lang))
        btn.clicked.connect(self._refresh_logs)
        row.addWidget(btn)
        v.addLayout(row)
        return w

    def _refresh_logs(self) -> None:
        text = _tail(log_path())
        self.log_view.setPlainText(text or _t("no_logs", self._lang))
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    # -- Compare engines -----------------------------------------------
    def _impl_engines(self) -> list:
        return [e for e in (self._catalog.get("engines") or [])
                if e.get("implemented")]

    def _runnable_model(self, engine_entry, lang):
        """(model_id, available). First AVAILABLE model serving *lang*, else any
        model serving *lang* (unavailable → reported at run), else the default."""
        models = engine_entry.get("models") or []
        serves = lambda m: (m.get("lang") is None) or (m.get("lang") == lang)
        avail = [m for m in models if serves(m) and m.get("available")]
        if avail:
            return avail[0]["id"], True
        anym = [m for m in models if serves(m)]
        if anym:
            return anym[0]["id"], False
        return engine_entry.get("default_model"), False

    def _build_compare_tab(self) -> QWidget:
        # Eight engines, each with its model on its own line, need ~900 px. Without
        # a scroll area the tab simply did not fit the dialog on a normal screen
        # and Qt compressed every row instead - buttons, the file field and the
        # table header came out with their text cut off.
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(area)
        w = QWidget()
        area.setWidget(w)
        v = QVBoxLayout(w)
        frow = QHBoxLayout()
        frow.addWidget(QLabel(_t("cmp_file", self._lang)))
        self.cmp_file = QLineEdit()
        self.cmp_file.setPlaceholderText(
            "C:\\path\\meeting.mkv" if self._lang == "en"
            else "C:\\путь\\встреча.mkv"
        )
        frow.addWidget(self.cmp_file, 1)
        b = QPushButton(_t("cmp_browse", self._lang)); b.clicked.connect(self._cmp_browse)
        frow.addWidget(b)
        frow.addWidget(QLabel(_t("cmp_lang", self._lang)))
        self.cmp_lang = QComboBox()
        self.cmp_lang.addItem("Русский", "ru"); self.cmp_lang.addItem("English", "en")
        self.cmp_lang.setCurrentIndex(0 if self._lang == "ru" else 1)
        self.cmp_lang.currentIndexChanged.connect(self._cmp_reload_engines)
        frow.addWidget(self.cmp_lang)
        v.addLayout(frow)

        self._cmp_checks = {}   # engine_id -> {"cb": QCheckBox, "sub": QLabel, ...}
        for e in self._impl_engines():
            cb = QCheckBox("")
            # The model name goes on its own wrapping line: a QCheckBox cannot
            # wrap, and engine label + model on one row made the dialog 2380 px
            # wide - the tab area then needed horizontal scrolling on any screen.
            sub = QLabel("")
            sub.setObjectName("hint")
            sub.setWordWrap(True)
            sub.setContentsMargins(24, 0, 0, 6)
            self._cmp_checks[e["id"]] = {"cb": cb, "sub": sub, "model": None, "entry": e}
            v.addWidget(cb)
            v.addWidget(sub)

        rrow = QHBoxLayout(); rrow.addStretch(1)
        self.cmp_run = QPushButton(_t("cmp_run", self._lang))
        self.cmp_run.setProperty("variant", "primary")
        self.cmp_run.clicked.connect(self._do_compare)
        rrow.addWidget(self.cmp_run); v.addLayout(rrow)
        self.cmp_status = QLabel(""); self.cmp_status.setObjectName("hint")
        v.addWidget(self.cmp_status)

        self.cmp_table = QTableWidget(0, 5)
        self.cmp_table.setHorizontalHeaderLabels([
            _t("cmp_engine", self._lang), _t("cmp_model", self._lang),
            _t("cmp_time", self._lang), _t("cmp_chars", self._lang),
            _t("cmp_status", self._lang)])
        self.cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cmp_table.itemSelectionChanged.connect(self._cmp_show_text)
        v.addWidget(self.cmp_table, 1)
        self.cmp_text = QTextBrowser()
        v.addWidget(self.cmp_text, 1)
        self._cmp_results = {}   # engine_id -> result dict
        self._cmp_reload_engines()
        return page

    def _cmp_reload_engines(self, *_) -> None:
        lang = self.cmp_lang.currentData() if hasattr(self, "cmp_lang") else self._lang
        for eid, info in self._cmp_checks.items():
            e = info["entry"]
            model, avail = self._runnable_model(e, lang)
            info["model"] = model
            info["available"] = avail
            label = (e.get("label") or {}).get(lang, eid)
            suffix = "" if avail else f"  · {_t('cmp_missing', self._lang)}"
            # A QCheckBox cannot wrap, so the full model directory name (some run
            # past 50 characters) made the row - and the whole dialog - 1600 px
            # wide. Shorten it on the control, keep it complete in the tooltip.
            info["cb"].setText(f"{label}{suffix}")
            info["cb"].setToolTip(label + chr(10) + model)
            info["sub"].setText(model)
            info["cb"].setChecked(bool(avail))

    def _cmp_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, _t("cmp_file", self._lang), "",
            "Media (*.mp4 *.mkv *.mov *.avi *.webm *.wav *.mp3 *.m4a);;All files (*)")
        if path:
            self.cmp_file.setText(path)

    def _do_compare(self) -> None:
        video = self.cmp_file.text().strip()
        if not video:
            self.cmp_status.setText(_t("cmp_pick_file", self._lang)); return
        if not Path(video).is_file():
            self.cmp_status.setText(
                "File not found." if self._lang == "en" else "Файл не найден."
            )
            return
        specs = [(eid, info["model"]) for eid, info in self._cmp_checks.items()
                 if info["cb"].isChecked() and info["model"]]
        if not specs:
            self.cmp_status.setText(_t("cmp_pick_engines", self._lang)); return
        self.cmp_run.setEnabled(False)
        self.cmp_status.setText(_t("cmp_running", self._lang))
        self.cmp_table.setRowCount(0)
        self._cmp_results = {}
        self.cmp_text.setHtml("")
        out_root = tempfile.mkdtemp(prefix="cmp_")
        self._cmp_worker = CompareWorker(
            self._python, self._processor, video, specs,
            self.cmp_lang.currentData(), "auto", out_root, parent=self)
        self._cmp_worker.engine_done.connect(self._cmp_on_engine)
        self._cmp_worker.finished_all.connect(self._cmp_on_all)
        self._cmp_worker.start()

    def _cmp_on_engine(self, engine, res) -> None:
        self._cmp_results[engine] = res
        r = self.cmp_table.rowCount()
        self.cmp_table.insertRow(r)
        label = next((( (i["entry"].get("label") or {}).get(self._lang, engine))
                      for eid, i in self._cmp_checks.items() if eid == engine), engine)
        status = _t("cmp_ok", self._lang) if res.get("ok") else (res.get("error") or "—")
        for col, text in enumerate([label, str(res.get("model", "")),
                                    str(res.get("seconds", "")), str(res.get("chars", "")),
                                    status]):
            item = QTableWidgetItem(text)
            item.setData(Qt.UserRole, engine)
            self.cmp_table.setItem(r, col, item)

    def _cmp_on_all(self, results) -> None:
        self.cmp_run.setEnabled(True)
        self.cmp_status.setText("")

    def _cmp_show_text(self) -> None:
        items = self.cmp_table.selectedItems()
        if not items:
            return
        engine = items[0].data(Qt.UserRole)
        res = self._cmp_results.get(engine)
        if res:
            body = (res.get("text") or res.get("error") or "").replace("\n", "<br>")
            self.cmp_text.setHtml(f"<div style='font-size:12px'>{body}</div>")

    # -- lifecycle -----------------------------------------------------
    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)

    def done(self, r):
        self._timer.stop()
        super().done(r)
