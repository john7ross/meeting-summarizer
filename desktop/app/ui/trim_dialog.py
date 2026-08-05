"""Trim dialog — split one recording into per-meeting segments before processing.

Back-to-back meetings often land in a single file; transcribing it whole yields a
summary/analysis that blends them. Here the user previews the video, marks a
start/end on the timeline, and adds one or more segments. Each segment is cut to
its own file and enters the queue as a separate job, so every meeting gets its
own transcript, summary and analysis.

The timeline is a real range control: drag the two handles to set start/end, or
click the track to seek the preview.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..backend.media import format_timecode, parse_timecode, probe_duration

_L = {
    "ru": {
        "title": "Выбор фрагментов для обработки",
        "intro": "Если в записи несколько встреч подряд — отметьте каждую как отдельный "
                 "фрагмент. Каждый фрагмент обрабатывается отдельно: свой транскрипт, "
                 "саммари и анализ.",
        "play": "▶ Воспроизвести", "pause": "⏸ Пауза",
        "start": "Начало:", "end": "Конец:",
        "from_here": "◀ Отсюда", "to_here": "Досюда ▶",
        "add": "＋ Добавить фрагмент", "remove": "Удалить фрагмент",
        "segments": "Фрагменты к обработке:",
        "whole": "Обработать файл целиком", "cancel": "Отмена",
        "ok": "Обработать фрагменты ({n})",
        "bad_time": "Некорректное время. Формат: 1:05:30, 05:30 или 90.",
        "bad_range": "Конец должен быть больше начала.",
        "none": "Добавьте хотя бы один фрагмент — или нажмите «Обработать файл целиком».",
        "dur": "длительность {d}",
    },
    "en": {
        "title": "Choose segments to process",
        "intro": "If the recording holds several back-to-back meetings, mark each as its "
                 "own segment. Every segment is processed separately: its own transcript, "
                 "summary and analysis.",
        "play": "▶ Play", "pause": "⏸ Pause",
        "start": "Start:", "end": "End:",
        "from_here": "◀ From here", "to_here": "To here ▶",
        "add": "＋ Add segment", "remove": "Remove segment",
        "segments": "Segments to process:",
        "whole": "Process the whole file", "cancel": "Cancel",
        "ok": "Process segments ({n})",
        "bad_time": "Invalid time. Use 1:05:30, 05:30 or 90.",
        "bad_range": "End must be greater than start.",
        "none": "Add at least one segment — or press “Process the whole file”.",
        "dur": "duration {d}",
    },
}


class RangeBar(QWidget):
    """Timeline with a playhead and two draggable handles (start / end)."""

    startChanged = Signal(float)
    endChanged = Signal(float)
    seekRequested = Signal(float)

    _HANDLE = 7   # px grab radius

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._pos = 0.0
        self._drag: Optional[str] = None

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, float(seconds))
        if self._end <= 0:
            self._end = self._duration
        self.update()

    def set_range(self, start: float, end: float) -> None:
        self._start, self._end = max(0.0, start), min(self._duration or end, end)
        self.update()

    def set_position(self, seconds: float) -> None:
        self._pos = max(0.0, float(seconds))
        self.update()

    def range(self) -> tuple:
        return self._start, self._end

    # -- geometry ------------------------------------------------------
    def _x(self, seconds: float) -> int:
        if self._duration <= 0:
            return 0
        return int(seconds / self._duration * max(1, self.width() - 1))

    def _t(self, x: int) -> float:
        if self._duration <= 0:
            return 0.0
        return max(0.0, min(self._duration,
                            x / max(1, self.width() - 1) * self._duration))

    # -- painting ------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_y, track_h = h // 2 - 5, 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(127, 127, 127, 70))
        p.drawRoundedRect(0, track_y, w, track_h, 5, 5)
        if self._duration > 0:
            x0, x1 = self._x(self._start), self._x(self._end)
            p.setBrush(QColor(0, 122, 204, 190))            # selected span
            p.drawRoundedRect(x0, track_y, max(2, x1 - x0), track_h, 5, 5)
            p.setBrush(QColor(0, 122, 204))                 # handles
            for x in (x0, x1):
                p.drawRoundedRect(x - 3, track_y - 6, 6, track_h + 12, 3, 3)
            p.setPen(QColor(230, 80, 60))                   # playhead
            px = self._x(self._pos)
            p.drawLine(px, track_y - 8, px, track_y + track_h + 8)
        p.end()

    # -- interaction ---------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._duration <= 0:
            return
        x = int(event.position().x())
        if abs(x - self._x(self._start)) <= self._HANDLE:
            self._drag = "start"
        elif abs(x - self._x(self._end)) <= self._HANDLE:
            self._drag = "end"
        else:
            self._drag = None
            self.seekRequested.emit(self._t(x))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._drag:
            return
        t = self._t(int(event.position().x()))
        if self._drag == "start":
            self._start = min(t, max(0.0, self._end - 1))
            self.startChanged.emit(self._start)
        else:
            self._end = max(t, self._start + 1)
            self.endChanged.emit(self._end)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None


class TrimDialog(QDialog):
    """Returns ``segments``: a list of (start, end) seconds. Empty list +
    ``whole_file`` True means 'process the original file unchanged'."""

    def __init__(self, video_path: str, language: str = "ru", parent=None):
        super().__init__(parent)
        self._lang = language if language in _L else "ru"
        self.video_path = str(video_path)
        self.segments: list = []
        self.whole_file = False

        self.setWindowTitle(self._t("title"))
        self.resize(880, 680)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("trimScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        intro = QLabel(self._t("intro"))
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # -- preview -----------------------------------------------------
        self.video = QVideoWidget()
        self.video.setMinimumHeight(300)
        root.addWidget(self.video, 1)

        self.player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self.player.setAudioOutput(self._audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)

        # -- transport + timeline ---------------------------------------
        row = QHBoxLayout()
        self.btn_play = QPushButton(self._t("play"))
        self.btn_play.clicked.connect(self._toggle_play)
        self.lbl_pos = QLabel("0:00:00 / 0:00:00")
        row.addWidget(self.btn_play)
        row.addWidget(self.lbl_pos)
        row.addStretch(1)
        root.addLayout(row)

        self.bar = RangeBar()
        self.bar.seekRequested.connect(lambda s: self.player.setPosition(int(s * 1000)))
        self.bar.startChanged.connect(lambda s: self.ed_start.setText(format_timecode(s)))
        self.bar.endChanged.connect(lambda s: self.ed_end.setText(format_timecode(s)))
        root.addWidget(self.bar)

        # -- start / end fields -----------------------------------------
        marks = QHBoxLayout()
        self.ed_start = QLineEdit("0:00:00")
        self.ed_start.setMaximumWidth(110)
        self.ed_end = QLineEdit("0:00:00")
        self.ed_end.setMaximumWidth(110)
        b_from = QPushButton(self._t("from_here"))
        b_from.clicked.connect(lambda: self._mark("start"))
        b_to = QPushButton(self._t("to_here"))
        b_to.clicked.connect(lambda: self._mark("end"))
        for w in (self.ed_start, self.ed_end):
            w.editingFinished.connect(self._fields_to_bar)
        marks.addWidget(QLabel(self._t("start")))
        marks.addWidget(self.ed_start)
        marks.addWidget(b_from)
        marks.addSpacing(16)
        marks.addWidget(QLabel(self._t("end")))
        marks.addWidget(self.ed_end)
        marks.addWidget(b_to)
        marks.addStretch(1)
        self.btn_add = QPushButton(self._t("add"))
        self.btn_add.setProperty("variant", "primary")
        self.btn_add.clicked.connect(self._add_segment)
        marks.addWidget(self.btn_add)
        root.addLayout(marks)

        # -- segment list -----------------------------------------------
        root.addWidget(QLabel(self._t("segments")))
        self.list = QListWidget()
        self.list.setMaximumHeight(120)
        root.addWidget(self.list)
        rm = QHBoxLayout()
        b_rm = QPushButton(self._t("remove"))
        b_rm.clicked.connect(self._remove_segment)
        rm.addWidget(b_rm)
        rm.addStretch(1)
        root.addLayout(rm)

        # -- actions ------------------------------------------------------
        actions = QHBoxLayout()
        b_whole = QPushButton(self._t("whole"))
        b_whole.clicked.connect(self._accept_whole)
        b_cancel = QPushButton(self._t("cancel"))
        b_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton(self._t("ok").format(n=0))
        self.btn_ok.setProperty("variant", "primary")
        self.btn_ok.clicked.connect(self._accept_segments)
        actions.addWidget(b_whole)
        actions.addStretch(1)
        actions.addWidget(b_cancel)
        actions.addWidget(self.btn_ok)
        # Keep the primary actions visible while the preview/segment editor
        # scrolls.  Previously they lived at the end of the scroll content and
        # disappeared below short (for example 768p) work areas.
        outer.addLayout(actions)

        # Probe duration up front: QMediaPlayer may not report it until it buffers,
        # and the range control is useless without a known length.
        dur = probe_duration(self.video_path)
        if dur > 0:
            self._set_duration(dur)
        self.player.setSource(QUrl.fromLocalFile(self.video_path))

    # -- helpers ---------------------------------------------------------
    def _t(self, key: str) -> str:
        return _L[self._lang].get(key, key)

    def _set_duration(self, seconds: float) -> None:
        self.duration = seconds
        self.bar.set_duration(seconds)
        if not self.segments:
            self.bar.set_range(0.0, seconds)
            self.ed_start.setText(format_timecode(0))
            self.ed_end.setText(format_timecode(seconds))
        self.lbl_pos.setText(f"0:00:00 / {format_timecode(seconds)}")

    def _on_duration(self, ms: int) -> None:
        if ms > 0 and getattr(self, "duration", 0) <= 0:
            self._set_duration(ms / 1000.0)

    def _on_position(self, ms: int) -> None:
        pos = ms / 1000.0
        self.bar.set_position(pos)
        self.lbl_pos.setText(
            f"{format_timecode(pos)} / {format_timecode(getattr(self, 'duration', 0))}")

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText(self._t("play"))
        else:
            self.player.play()
            self.btn_play.setText(self._t("pause"))

    def _mark(self, which: str) -> None:
        """Set start/end from the current playhead."""
        pos = self.player.position() / 1000.0
        (self.ed_start if which == "start" else self.ed_end).setText(format_timecode(pos))
        self._fields_to_bar()

    def _read_fields(self) -> tuple:
        return parse_timecode(self.ed_start.text()), parse_timecode(self.ed_end.text())

    def _fields_to_bar(self) -> None:
        try:
            start, end = self._read_fields()
        except ValueError:
            return
        self.bar.set_range(start, end)

    def _add_segment(self) -> None:
        try:
            start, end = self._read_fields()
        except ValueError:
            QMessageBox.warning(self, self._t("title"), self._t("bad_time"))
            return
        if end <= start:
            QMessageBox.warning(self, self._t("title"), self._t("bad_range"))
            return
        self.segments.append((start, end))
        item = QListWidgetItem(
            f"{format_timecode(start)} — {format_timecode(end)}   "
            f"({self._t('dur').format(d=format_timecode(end - start))})")
        self.list.addItem(item)
        self.btn_ok.setText(self._t("ok").format(n=len(self.segments)))

    def _remove_segment(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        self.list.takeItem(row)
        del self.segments[row]
        self.btn_ok.setText(self._t("ok").format(n=len(self.segments)))

    def _accept_whole(self) -> None:
        self.player.stop()
        self.segments = []
        self.whole_file = True
        self.accept()

    def _accept_segments(self) -> None:
        if not self.segments:
            QMessageBox.information(self, self._t("title"), self._t("none"))
            return
        self.player.stop()
        self.whole_file = False
        self.accept()

    def done(self, r) -> None:      # ensure the file handle is released
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:           # noqa: BLE001
            pass
        super().done(r)
