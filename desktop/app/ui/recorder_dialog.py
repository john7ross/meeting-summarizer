"""Recorder dialog — capture a meeting live from the microphone (and the call).

On stop the WAV is handed to the normal add-file flow, so it goes through the
trim dialog (split into per-meeting segments) and then into the queue exactly
like a dropped file.

While recording, two optional panels run alongside: the live transcript and a
rolling live summary. Both are drafts — the queue still transcribes the file
properly afterwards with the configured engine — and both are written to disk as
they go, so closing this window does not lose them.

The toggles here write straight into settings.json rather than keeping a private
copy: the same three switches appear in the settings dialog, and two places
holding their own idea of whether live is on is exactly how a feature ends up
"enabled" in one window and off in the other.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..core.live_session import LiveSession
from ..core.loopback import default_output_name, probe as probe_loopback
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
        "use_live": "В обработку по live-тексту",
        "use_live_tip": "Взять уже распознанный live-транскрипт как текст встречи и сразу считать саммари и анализ, не транскрибируя запись заново. Набор артефактов тот же, что и при обычной обработке.",
        "discard_q": "Идёт запись. Остановить и удалить её?",
        "err": "Не удалось начать запись: {err}",
        "capture_failed": "Запись остановлена: {err}",
        "system_audio": "Писать системный звук",
        "system_audio_hint": "Вторым каналом пишется то, что звучит из динамиков "
                             "(собеседники в звонке). Устройство: {name}",
        "system_audio_off": "Системный звук недоступен: {reason}",
        "system_reason_no_package": "не установлен пакет soundcard",
        "system_reason_no_device": "нет устройства вывода с поддержкой захвата",
        "system_reason_format": "микрофон отдаёт формат, непригодный для сведения",
        "system_live": "Запись: микрофон + системный звук",
        "system_live_mic": "Запись: только микрофон",
        "live_transcription": "Live-транскрибация",
        "live_summary": "Live-саммари",
        "live_summary_needs": "Доступно только при включённой live-транскрибации",
        "tab_transcript": "Транскрипция", "tab_summary": "Live Summary",
        "waiting_speech": "Ожидание речи...",
        "waiting_summary": "Ожидание речи для саммари...",
        "live_loading": "Загрузка модели распознавания...",
        "live_ready": "Live-транскрибация: {info}",
        "live_lag": "Распознавание отстаёт — сводка обновится позже",
        "live_error": "Live-транскрибация недоступна: {err}",
        "live_warning": "Предупреждение: {err}",
        "live_done": "Live-транскрибация завершена",
        "sum_updating": "Обновление сводки...",
        "sum_regen": "Полная пересборка сводки...",
        "sum_consolidate": "Консолидация сводки...",
        "sum_error": "Не удалось обновить сводку: {err}",
        "sum_limit": "Достигнут лимит обновлений сводки",
        "sum_updates": "Обновлений сводки: {n}",
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
        "use_live": "Process from live text",
        "use_live_tip": "Take the live transcript as the meeting text and go straight to summary and analysis, without transcribing the recording again. The artifacts produced are the same as in normal processing.",
        "discard_q": "Recording in progress. Stop and discard it?",
        "err": "Could not start recording: {err}",
        "capture_failed": "Recording stopped: {err}",
        "system_audio": "Record system audio",
        "system_audio_hint": "The second channel records what your speakers play "
                             "(the other people on the call). Device: {name}",
        "system_audio_off": "System audio unavailable: {reason}",
        "system_reason_no_package": "the soundcard package is not installed",
        "system_reason_no_device": "no output device supports loopback capture",
        "system_reason_format": "the microphone format cannot be mixed",
        "system_live": "Recording: microphone + system audio",
        "system_live_mic": "Recording: microphone only",
        "live_transcription": "Live transcription",
        "live_summary": "Live summary",
        "live_summary_needs": "Available only with live transcription enabled",
        "tab_transcript": "Transcript", "tab_summary": "Live Summary",
        "waiting_speech": "Waiting for speech...",
        "waiting_summary": "Waiting for speech to summarise...",
        "live_loading": "Loading the recognition model...",
        "live_ready": "Live transcription: {info}",
        "live_lag": "Recognition is behind - the summary will catch up",
        "live_error": "Live transcription unavailable: {err}",
        "live_warning": "Warning: {err}",
        "live_done": "Live transcription finished",
        "sum_updating": "Updating the summary...",
        "sum_regen": "Rebuilding the summary...",
        "sum_consolidate": "Consolidating the summary...",
        "sum_error": "Could not update the summary: {err}",
        "sum_limit": "Summary update limit reached",
        "sum_updates": "Summary updates: {n}",
    },
}


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class RecorderDialog(QDialog):
    """``result_path`` holds the recorded WAV when the user chooses to process it."""

    def __init__(self, root, language: str = "ru", parent=None, settings=None,
                 on_settings_changed=None):
        super().__init__(parent)
        self._lang = language if language in _L else "ru"
        self._root = root
        self._settings = settings if isinstance(settings, dict) else {}
        self._on_settings_changed = on_settings_changed
        self.result_path: str = ""
        self._rec = AudioRecorder(self)
        self._rec.level.connect(self._on_level)
        self._rec.tick.connect(self._on_tick)
        self._rec.failed.connect(self._on_failed)
        self._rec.pcm.connect(self._on_pcm)
        self._live = LiveSession(self)
        self._live.segment.connect(self._on_segment)
        self._live.status.connect(self._on_live_status)
        self._live.summary.connect(self._on_live_summary)
        self._loopback_ok, self._loopback_reason = probe_loopback()
        # Set when the user chooses to process the recording from the text live
        # mode already produced, instead of transcribing the file again.
        self.use_live_transcript = False
        self.live_transcript_path = ""

        self.setWindowTitle(self._t("title"))
        self.setMinimumWidth(620)
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

        # -- optional capture / live toggles -----------------------------
        self.chk_system = QCheckBox(self._t("system_audio"))
        self.chk_system.setChecked(bool(self._settings.get("recordSystemAudio"))
                                   and self._loopback_ok)
        self.chk_system.setEnabled(self._loopback_ok)
        self.chk_system.toggled.connect(
            lambda on: self._save_setting("recordSystemAudio", bool(on)))
        root_l.addWidget(self.chk_system)

        self.lbl_system = QLabel("")
        self.lbl_system.setObjectName("hint")
        self.lbl_system.setWordWrap(True)
        root_l.addWidget(self.lbl_system)

        self.chk_live = QCheckBox(self._t("live_transcription"))
        self.chk_live.setChecked(bool(self._settings.get("liveTranscription")))
        self.chk_live.toggled.connect(self._on_live_toggled)
        root_l.addWidget(self.chk_live)

        self.chk_summary = QCheckBox(self._t("live_summary"))
        self.chk_summary.setChecked(bool(self._settings.get("liveSummary")))
        self.chk_summary.toggled.connect(
            lambda on: self._save_setting("liveSummary", bool(on)))
        root_l.addWidget(self.chk_summary)

        self.lbl_time = QLabel(self._t("elapsed").format(t="0:00:00"))
        self.lbl_time.setObjectName("sectionTitle")
        root_l.addWidget(self.lbl_time)

        self.lbl_saved = QLabel("")
        self.lbl_saved.setObjectName("hint")
        self.lbl_saved.setWordWrap(True)
        root_l.addWidget(self.lbl_saved)

        # -- live panels --------------------------------------------------
        self.tabs = QTabWidget()
        self.txt_transcript = QPlainTextEdit()
        self.txt_transcript.setReadOnly(True)
        self.txt_transcript.setPlaceholderText(self._t("waiting_speech"))
        self.txt_summary = QPlainTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setPlaceholderText(self._t("waiting_summary"))
        self.tabs.addTab(self._panel(self.txt_transcript), self._t("tab_transcript"))
        self.tabs.addTab(self._panel(self.txt_summary), self._t("tab_summary"))
        self.tabs.setMinimumHeight(220)
        root_l.addWidget(self.tabs, 1)

        # The status line lives NEXT TO the panels, never inside them: replacing
        # the summary text with "updating..." is how a live summary appears to
        # vanish every time it refreshes.
        self.lbl_live_status = QLabel("")
        self.lbl_live_status.setObjectName("hint")
        self.lbl_live_status.setWordWrap(True)
        root_l.addWidget(self.lbl_live_status)

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
        self.btn_use_live = QPushButton(self._t("use_live"))
        self.btn_use_live.setToolTip(self._t("use_live_tip"))
        self.btn_use_live.setEnabled(False)
        self.btn_use_live.setVisible(False)
        self.btn_use_live.clicked.connect(self._accept_with_live_transcript)
        btns.addWidget(self.btn_use_live)
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
        self._sync_live_controls()
        self._update_system_hint()

    def _panel(self, widget) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        return holder

    def _t(self, key: str) -> str:
        return _L[self._lang].get(key, key)

    # -- settings ---------------------------------------------------------
    def _save_setting(self, key: str, value) -> None:
        """Persist one toggle. The dialog owns no state the settings file does not."""
        self._settings[key] = value
        if callable(self._on_settings_changed):
            self._on_settings_changed(dict(self._settings))

    def _on_live_toggled(self, on: bool) -> None:
        self._save_setting("liveTranscription", bool(on))
        if not on and self.chk_summary.isChecked():
            # No transcript stream means no summary input. Turning it off here
            # (and saving that) is honest; leaving it "on" but dead is not.
            self.chk_summary.setChecked(False)
        self._sync_live_controls()

    def _sync_live_controls(self) -> None:
        live_on = self.chk_live.isChecked()
        self.chk_summary.setEnabled(live_on)
        self.chk_summary.setToolTip("" if live_on else self._t("live_summary_needs"))
        self.tabs.setVisible(live_on)
        self.tabs.setTabVisible(1, live_on and self.chk_summary.isChecked())
        if not live_on:
            self.lbl_live_status.setText("")

    def _update_system_hint(self) -> None:
        if not self._loopback_ok:
            reason = {
                "no-package": self._t("system_reason_no_package"),
                "no-device": self._t("system_reason_no_device"),
            }.get(self._loopback_reason, self._loopback_reason)
            self.lbl_system.setText(self._t("system_audio_off").format(reason=reason))
            return
        if self.chk_system.isChecked():
            self.lbl_system.setText(self._t("system_audio_hint").format(
                name=default_output_name() or "-"))
        else:
            self.lbl_system.setText("")

    # -- recording -------------------------------------------------------
    def _accept_with_live_transcript(self) -> None:
        self.use_live_transcript = True
        self.accept()

    def _offer_live_processing(self) -> None:
        """Offer the shortcut only when live really produced a usable transcript.

        A recording with live switched off, or one where the engine never came up,
        must not show a button that would queue an empty transcript.
        """
        path = Path(self._live.transcript_path) if self._live.transcript_path else None
        usable = bool(path and path.is_file()
                      and path.read_text(encoding="utf-8", errors="replace").strip())
        self.live_transcript_path = str(path) if usable else ""
        self.btn_use_live.setVisible(usable)
        self.btn_use_live.setEnabled(usable)

    def _toggle_record(self) -> None:
        if self._rec.recording:
            self._live.stop()
            path = self._rec.stop()
            self.result_path = path
            self.btn_rec.setText(self._t("rec"))
            self.btn_pause.setEnabled(False)
            self.cb_device.setEnabled(True)
            self._set_toggles_enabled(True)
            self.meter.setValue(0)
            if path:
                self.lbl_saved.setText(self._t("saved").format(name=Path(path).name))
                self.btn_use.setEnabled(True)
                self._offer_live_processing()
            return
        try:
            path = default_recording_path(self._root)
            self._rec.start(path, self.cb_device.currentData(),
                            capture_system=self.chk_system.isChecked())
        except Exception as exc:   # noqa: BLE001
            QMessageBox.warning(self, self._t("title"), self._t("err").format(err=exc))
            return
        self.result_path = ""
        self.use_live_transcript = False
        self.live_transcript_path = ""
        self.btn_use.setEnabled(False)
        self.btn_use_live.setVisible(False)
        self.btn_use_live.setEnabled(False)
        self.txt_transcript.clear()
        self.txt_summary.clear()
        self.lbl_live_status.setText("")
        self.lbl_saved.setText(self._t("system_live") if self._rec.system_active
                               else self._t("system_live_mic"))
        self.btn_rec.setText(self._t("stop"))
        self.btn_pause.setEnabled(True)
        self.cb_device.setEnabled(False)
        self._set_toggles_enabled(False)
        self._update_system_hint()
        if self.chk_live.isChecked():
            self._start_live(path)

    def _set_toggles_enabled(self, enabled: bool) -> None:
        """The capture layout is fixed for the duration of one recording."""
        self.chk_system.setEnabled(enabled and self._loopback_ok)
        self.chk_live.setEnabled(enabled)
        self.chk_summary.setEnabled(enabled and self.chk_live.isChecked())

    def _start_live(self, recording_path) -> None:
        settings = dict(self._settings)
        settings["liveSummary"] = self.chk_summary.isChecked()
        stem = Path(recording_path).with_suffix("")
        self.lbl_live_status.setText(self._t("live_loading"))
        started = self._live.start(settings, stem,
                                   channels=self._rec.channels,
                                   sample_rate=16000)
        if not started:
            self.tabs.setTabVisible(1, False)

    def _on_pcm(self, pcm: bytes, channels: int, rate: int) -> None:
        self._live.feed(pcm, channels, rate)

    def _toggle_pause(self) -> None:
        self._rec.set_paused(not self._rec.paused)
        self.btn_pause.setText(self._t("resume") if self._rec.paused else self._t("pause"))

    def _on_level(self, value: float) -> None:
        self.meter.setValue(int(value * 100))

    def _on_tick(self, seconds: float) -> None:
        self.lbl_time.setText(self._t("elapsed").format(t=_fmt(seconds)))

    def _on_failed(self, error: str) -> None:
        self._live.stop()
        self.result_path = ""
        self.btn_rec.setText(self._t("rec"))
        self.btn_pause.setEnabled(False)
        self.btn_use.setEnabled(False)
        self.cb_device.setEnabled(True)
        self._set_toggles_enabled(True)
        self.meter.setValue(0)
        self.lbl_saved.setText(self._t("capture_failed").format(err=error))

    # -- live panels ------------------------------------------------------
    def _on_segment(self, segment) -> None:
        self.txt_transcript.appendPlainText(segment.line)
        bar = self.txt_transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_live_status(self, kind: str, message: str) -> None:
        if kind == "ready":
            self.lbl_live_status.setText(self._t("live_ready").format(info=message))
            return
        if kind == "lag":
            self.lbl_live_status.setText(self._t("live_lag"))
        elif kind == "error":
            self.lbl_live_status.setText(self._t("live_error").format(err=message))
        elif kind == "warning":
            self.lbl_live_status.setText(self._t("live_warning").format(err=message))
        elif kind == "done":
            self.lbl_live_status.setText(self._t("live_done"))

    def _on_live_summary(self, text: str, status: str) -> None:
        # The text is always the last GOOD summary; status is reported separately.
        if text and text != self.txt_summary.toPlainText():
            self.txt_summary.setPlainText(text)
        if not status:
            if self._live.updates:
                self.lbl_live_status.setText(
                    self._t("sum_updates").format(n=self._live.updates))
            return
        if status.startswith("updating:"):
            mode = status.split(":", 1)[1]
            self.lbl_live_status.setText({
                "regen": self._t("sum_regen"),
                "consolidate": self._t("sum_consolidate"),
            }.get(mode, self._t("sum_updating")))
        elif status.startswith("error:"):
            self.lbl_live_status.setText(
                self._t("sum_error").format(err=status.split(":", 1)[1]))
        elif status == "limit":
            self.lbl_live_status.setText(self._t("sum_limit"))

    # -- lifecycle -------------------------------------------------------
    def reject(self) -> None:
        if self._rec.recording:
            ans = QMessageBox.question(self, self._t("title"), self._t("discard_q"))
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._live.stop()
            self._rec.stop()
        super().reject()

    def done(self, r) -> None:
        if self._rec.recording:
            self._live.stop()
            self.result_path = self._rec.stop()
            self._offer_live_processing()
        super().done(r)
