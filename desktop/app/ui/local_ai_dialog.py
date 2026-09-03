"""Built-in local AI — one-click setup for users who don't run their own LLM.

Downloads the current llama.cpp server and a curated chat model, starts it, and
points the app at it. Nothing here ships in the distribution (several GB); it is
fetched on demand into ``resources/local_ai/``.
"""
from __future__ import annotations

import json
import subprocess

from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout)

from .. import paths
from ..core.worker import ModelsWorker

_L = {
    "ru": {
        "title": "Встроенный локальный ИИ",
        "intro": "Если у вас нет своей локальной нейросети — приложение может скачать и "
                 "запустить её само. Тогда саммари и анализ будут работать полностью "
                 "офлайн, без облака, ключей и настройки. Загрузка разовая.",
        "status": "Состояние:", "model": "Модель:",
        "install": "Скачать и установить", "start": "Запустить", "stop": "Остановить",
        "close": "Закрыть",
        "st_none": "не установлен",
        "st_ready": "установлен, не запущен",
        "st_running": "работает на порту {port} — приложение использует его",
        "gpu_yes": "видеокарта NVIDIA, {v} ГБ — рекомендуется «{m}»",
        "gpu_no": "видеокарты нет, будет работать на процессоре (медленно) — "
                  "рекомендуется «{m}»",
        "installing": "Загрузка… это разовая операция, можно свернуть окно",
        "done_install": "Готово. Теперь нажмите «Запустить».",
        "starting": "Запускаю сервер и загружаю модель в память…",
        "done_start": "ИИ запущен. Приложение переключено на него.",
        "done_start_reasoning": "ИИ запущен, приложение переключено на него. Модель поддерживает reasoning — для скорости «Отключить reasoning» включено автоматически "
                                "(можно вернуть в Настройках).",
        "custom": "Или своя модель:",
        "custom_ph": "ссылка на .gguf (HuggingFace) — если своя модель, а не из списка",
        "stopped": "Остановлен.",
        "err": "Ошибка: {err}",
        "confirm_dl": "Будет загружено примерно {gb} ГБ (движок + модель). Продолжить?",
    },
    "en": {
        "title": "Built-in local AI",
        "intro": "If you don't run your own local model, the app can download and start "
                 "one for you. Summary and analysis then work fully offline — no cloud, "
                 "no API keys, no setup. A one-time download.",
        "status": "Status:", "model": "Model:",
        "install": "Download & install", "start": "Start", "stop": "Stop",
        "close": "Close",
        "st_none": "not installed",
        "st_ready": "installed, not running",
        "st_running": "running on port {port} — the app is using it",
        "gpu_yes": "NVIDIA GPU, {v} GB — “{m}” recommended",
        "gpu_no": "no GPU, will run on the CPU (slow) — “{m}” recommended",
        "installing": "Downloading… one-time operation, you can minimise this window",
        "done_install": "Done. Now press “Start”.",
        "starting": "Starting the server and loading the model…",
        "done_start": "AI started. The app now uses it.",
        "done_start_reasoning": "AI started, the app now uses it. This is a reasoning model, so \"disable reasoning\" was turned on for speed "
                                "(you can re-enable it in Settings).",
        "custom": "Or your own model:",
        "custom_ph": "a .gguf URL (HuggingFace) — for a model not in the list",
        "stopped": "Stopped.",
        "err": "Error: {err}",
        "confirm_dl": "About {gb} GB will be downloaded (engine + model). Continue?",
    },
}


class LocalAiDialog(QDialog):
    """Manages the built-in llama.cpp server. On a successful start it rewires
    ``settings`` to use it (provider=local + endpoint), and the caller saves."""

    def __init__(self, settings: dict, language: str = "ru", parent=None):
        super().__init__(parent)
        self._lang = language if language in _L else "ru"
        self.settings = settings
        self._worker = None
        self._status: dict = {}

        self.setWindowTitle(self._t("title"))
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)

        intro = QLabel(self._t("intro"))
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        v.addWidget(intro)

        self.lbl_status = QLabel("…")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        row = QHBoxLayout()
        row.addWidget(QLabel(self._t("model")))
        self.cb_model = QComboBox()
        row.addWidget(self.cb_model, 1)
        v.addLayout(row)

        # Not limited to the curated list: paste any GGUF URL (HuggingFace, etc.).
        crow = QHBoxLayout()
        crow.addWidget(QLabel(self._t("custom")))
        self.ed_custom = QLineEdit()
        self.ed_custom.setPlaceholderText(self._t("custom_ph"))
        crow.addWidget(self.ed_custom, 1)
        v.addLayout(crow)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.lbl_note = QLabel("")
        self.lbl_note.setObjectName("hint")
        self.lbl_note.setWordWrap(True)
        v.addWidget(self.lbl_note)

        btns = QHBoxLayout()
        self.btn_install = QPushButton(self._t("install"))
        self.btn_install.setProperty("variant", "primary")
        self.btn_install.clicked.connect(self._install)
        self.btn_start = QPushButton(self._t("start"))
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton(self._t("stop"))
        self.btn_stop.clicked.connect(self._stop)
        btns.addWidget(self.btn_install)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        b_close = QPushButton(self._t("close"))
        b_close.clicked.connect(self.accept)
        btns.addWidget(b_close)
        v.addLayout(btns)

        self._refresh()

    def _t(self, key: str) -> str:
        return _L[self._lang].get(key, key)

    # -- backend calls ---------------------------------------------------
    def _cli(self, *args) -> list:
        return [str(paths.python_executable()), str(paths.LOCAL_AI_SCRIPT), *args]

    def _run_sync(self, *args) -> dict:
        """Short call (status/catalog/start/stop) — returns the 'result' payload."""
        env = dict(__import__("os").environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(self._cli(*args), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300, env=env)
        last = (proc.stdout or "").strip().splitlines()
        if not last:
            raise RuntimeError((proc.stderr or "no output")[-200:])
        payload = json.loads(last[-1])
        if payload.get("event") == "error":
            raise RuntimeError(payload.get("error", "unknown error"))
        return payload.get("result", {})

    def _refresh(self) -> None:
        try:
            self._status = self._run_sync("status", "--port", str(self._port()))
        except Exception as exc:      # noqa: BLE001
            self.lbl_status.setText(self._t("err").format(err=exc))
            return
        st = self._status
        rec = st.get("recommended", "")
        # model picker (recommended marked, installed marked)
        cat = self._run_sync("catalog").get("catalog", {}) if not self.cb_model.count() else None
        if cat:
            for key, info in cat.items():
                mark = "  ★" if key == rec else ""
                have = "  ✓" if st["models"].get(key) else ""
                self.cb_model.addItem(f"{info['label']}{mark}{have}", key)
            idx = self.cb_model.findData(rec)
            self.cb_model.setCurrentIndex(max(0, idx))
        hw = (self._t("gpu_yes").format(v=st.get("vram_gb"), m=rec) if st.get("gpu")
              else self._t("gpu_no").format(m=rec))
        if st.get("running"):
            state = self._t("st_running").format(port=st.get("port"))
        elif st.get("engine_installed"):
            state = self._t("st_ready")
        else:
            state = self._t("st_none")
        self.lbl_status.setText(f"{self._t('status')} {state}\n{hw}")
        self.btn_start.setEnabled(st.get("engine_installed") and not st.get("running"))
        self.btn_stop.setEnabled(bool(st.get("running")))
        self.btn_install.setEnabled(not st.get("running"))

    def _port(self) -> int:
        try:
            return int(self.settings.get("localAiPort") or 8081)
        except (TypeError, ValueError):
            return 8081

    def _custom_url(self) -> str:
        return self.ed_custom.text().strip()

    def _catalog(self) -> dict:
        return (self._status.get("__catalog__")
                or self._run_sync("catalog").get("catalog", {}))

    # -- actions ---------------------------------------------------------
    def _install(self) -> None:
        url = self._custom_url()
        model = "" if url else self.cb_model.currentData()
        if url:
            gb = 5
        else:
            gb = {"qwen3-4b": 2.6, "qwen3-8b": 5.2, "qwen3-14b": 8.8,
                  "qwen3-30b-a3b": 17.8, "gemma3-4b": 2.6, "gemma3-12b": 7.2}.get(model, 5)
        if QMessageBox.question(self, self._t("title"),
                                self._t("confirm_dl").format(gb=gb)) != \
                QMessageBox.StandardButton.Yes:
            return
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.lbl_note.setText(self._t("installing"))
        self._set_busy(True)
        cli = self._cli("install", "--port", str(self._port()))
        cli += ["--url", url] if url else ["--model", model]
        self._worker = ModelsWorker(cli, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_installed)
        self._worker.start()

    def _on_progress(self, percent: int, detail: str) -> None:
        self.bar.setValue(max(0, min(100, percent)))
        if detail:
            self.lbl_note.setText(detail)

    def _on_installed(self, ok: bool, result, error: str) -> None:
        self._set_busy(False)
        self.bar.setVisible(False)
        self.lbl_note.setText(self._t("done_install") if ok
                              else self._t("err").format(err=error))
        self._refresh()

    def _start(self) -> None:
        url = self._custom_url()
        model = (url.split("?")[0].split("/")[-1]) if url else self.cb_model.currentData()
        self.lbl_note.setText(self._t("starting"))
        self._set_busy(True)
        try:
            self._run_sync("start", "--model", model, "--port", str(self._port()))
        except Exception as exc:      # noqa: BLE001
            self._set_busy(False)
            self.lbl_note.setText(self._t("err").format(err=exc))
            self._refresh()
            return
        # Point the app at the built-in server.
        self.settings["aiProvider"] = "local"
        self.settings["localEndpoint"] = f"http://127.0.0.1:{self._port()}/v1"
        self.settings["localAiPort"] = self._port()
        self.settings["localAiModel"] = model
        self.settings["llamaPort"] = self._port()   # VRAM hand-off targets it too
        # Reasoning models "think out loud" and are slow on long transcripts, so
        # for this one-click audience turn reasoning OFF by default (the user can
        # re-enable it in Settings). Non-reasoning models leave it untouched.
        note = "done_start"
        entry = self._catalog().get(model) or {}
        if entry.get("reasoning"):
            self.settings["disableReasoning"] = True
            note = "done_start_reasoning"
        self._set_busy(False)
        self.lbl_note.setText(self._t(note))
        self._refresh()

    def _stop(self) -> None:
        try:
            self._run_sync("stop", "--port", str(self._port()))
            self.lbl_note.setText(self._t("stopped"))
        except Exception as exc:      # noqa: BLE001
            self.lbl_note.setText(self._t("err").format(err=exc))
        self._refresh()

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_install, self.btn_start, self.btn_stop):
            b.setEnabled(not busy)
