"""Recorder dialog — capture a meeting live from the microphone.

On stop the WAV is handed to the normal add-file flow, so it goes through the
trim dialog (split into per-meeting segments) and then into the queue exactly
like a dropped file.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout)

from ..core.recorder import AudioRecorder, default_recording_path, input_devices

_L = {
    "ru": {
        "title": "Запись с микрофона",
        "intro": "Запишите встречу с микрофона. После остановки запись попадёт на нарезку "
                 "(если встреч несколько) и затем в очередь обработки.",
        "device": "Микрофон:", "level": "Уровень:",
        "rec": "● Начать запись", "stop": "■ Остановить",
        "pause": "❚❚ Пауза", "resume": "▶ Продолжить",
        "elapsed": "Записано: {t}",
        "saved": "Сохранено: {name}",
        "no_device": "Микрофон не найден. Подключите устройство записи.",
        "close": "Закрыть", "use": "В обработку",
        "discard_q": "Идёт запись. Остановить и удалить её?",
        "err": "Не удалось начать запись: {err}",
        "capture_failed": "Запись остановлена: {err}",
    },
    "en": {
        "title": "Record from microphone",
        "intro": "Record the meeting from your microphone. On stop it goes to the trim "
                 "dialog (if it holds several meetings) and then into the queue.",
        "device": "Microphone:", "level": "Level:",
        "rec": "● Start recording", "stop": "■ Stop",
        "pause": "❚❚ Pause", "resume": "▶ Resume",
        "elapsed": "Recorded: {t}",
        "saved": "Saved: {name}",
        "no_device": "No microphone found. Connect a recording device.",
        "close": "Close", "use": "Process",
        "discard_q": "Recording in progress. Stop and discard it?",
        "err": "Could not start recording: {err}",
        "capture_failed": "Recording stopped: {err}",
    },
}


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class RecorderDialog(QDialog):
    """``result_path`` holds the recorded WAV when the user chooses to process it."""

    def __init__(self, root, language: str = "ru", parent=None):
        super().__init__(parent)
        self._lang = language if language in _L else "ru"
        self._root = root
        self.result_path: str = ""
        self._rec = AudioRecorder(self)
        self._rec.level.connect(self._on_level)
        self._rec.tick.connect(self._on_tick)
        self._rec.failed.connect(self._on_failed)

        self.setWindowTitle(self._t("title"))
        self.setMinimumWidth(520)
        root_l = QVBoxLayout(self)

        intro = QLabel(self._t("intro"))
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        root_l.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel(self._t("device")))
        self.cb_device = QComboBox()
        for dev in input_devices():
            self.cb_device.addItem(dev.description(), dev)
        row.addWidget(self.cb_device, 1)
        root_l.addLayout(row)

        lvl = QHBoxLayout()
        lvl.addWidget(QLabel(self._t("level")))
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(14)
        lvl.addWidget(self.meter, 1)
        root_l.addLayout(lvl)

        self.lbl_time = QLabel(self._t("elapsed").format(t="0:00:00"))
        self.lbl_time.setObjectName("sectionTitle")
        root_l.addWidget(self.lbl_time)

        self.lbl_saved = QLabel("")
        self.lbl_saved.setObjectName("hint")
        root_l.addWidget(self.lbl_saved)

        btns = QHBoxLayout()
        self.btn_rec = QPushButton(self._t("rec"))
        self.btn_rec.setProperty("variant", "primary")
        self.btn_rec.clicked.connect(self._toggle_record)
        self.btn_pause = QPushButton(self._t("pause"))
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        btns.addWidget(self.btn_rec)
        btns.addWidget(self.btn_pause)
        btns.addStretch(1)
        self.btn_use = QPushButton(self._t("use"))
        self.btn_use.setProperty("variant", "primary")
        self.btn_use.setEnabled(False)
        self.btn_use.clicked.connect(self.accept)
        self.btn_close = QPushButton(self._t("close"))
        self.btn_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_use)
        btns.addWidget(self.btn_close)
        root_l.addLayout(btns)

        if self.cb_device.count() == 0:
            self.btn_rec.setEnabled(False)
            self.lbl_saved.setText(self._t("no_device"))

    def _t(self, key: str) -> str:
        return _L[self._lang].get(key, key)

    # -- recording -------------------------------------------------------
    def _toggle_record(self) -> None:
        if self._rec.recording:
            path = self._rec.stop()
            self.result_path = path
            self.btn_rec.setText(self._t("rec"))
            self.btn_pause.setEnabled(False)
            self.cb_device.setEnabled(True)
            self.meter.setValue(0)
            if path:
                self.lbl_saved.setText(self._t("saved").format(name=Path(path).name))
                self.btn_use.setEnabled(True)
            return
        try:
            path = default_recording_path(self._root)
            self._rec.start(path, self.cb_device.currentData())
        except Exception as exc:   # noqa: BLE001
            QMessageBox.warning(self, self._t("title"), self._t("err").format(err=exc))
            return
        self.result_path = ""
        self.btn_use.setEnabled(False)
        self.lbl_saved.setText("")
        self.btn_rec.setText(self._t("stop"))
        self.btn_pause.setEnabled(True)
        self.cb_device.setEnabled(False)

    def _toggle_pause(self) -> None:
        self._rec.set_paused(not self._rec.paused)
        self.btn_pause.setText(self._t("resume") if self._rec.paused else self._t("pause"))

    def _on_level(self, value: float) -> None:
        self.meter.setValue(int(value * 100))

    def _on_tick(self, seconds: float) -> None:
        self.lbl_time.setText(self._t("elapsed").format(t=_fmt(seconds)))

    def _on_failed(self, error: str) -> None:
        self.result_path = ""
        self.btn_rec.setText(self._t("rec"))
        self.btn_pause.setEnabled(False)
        self.btn_use.setEnabled(False)
        self.cb_device.setEnabled(True)
        self.meter.setValue(0)
        self.lbl_saved.setText(self._t("capture_failed").format(err=error))

    # -- lifecycle -------------------------------------------------------
    def reject(self) -> None:
        if self._rec.recording:
            ans = QMessageBox.question(self, self._t("title"), self._t("discard_q"))
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._rec.stop()
        super().reject()

    def done(self, r) -> None:
        if self._rec.recording:
            self.result_path = self._rec.stop()
        super().done(r)
