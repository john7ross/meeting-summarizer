"""Processing history: the journal of runs, not the queue.

The queue table lists MEETINGS and is rebuilt from the archive, so removing a row
took the only trace of the processing with it. This window reads the separate
journal (``config/processing_history.json``, see :mod:`app.core.run_history`) and
answers what the queue never could: when a file was processed, how long it took,
which stages ran with their timings, what it produced and — for a failed run —
the whole error text, wrapped, never in a horizontal scrollbar.

Left: one row per run, newest first. Right: everything recorded for the selected
run. Deleting a meeting from the queue does not touch either.
"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..core.models import JobStatus, main_label, source_badge, source_label
from ..core.run_history import INTERRUPTED

_L = {
    "ru": {
        "title": "История обработки",
        "hint": "Журнал прогонов. Удаление встречи из очереди его не затрагивает.",
        "col_file": "Файл", "col_kind": "Что делали", "col_started": "Начало",
        "col_duration": "Длительность", "col_status": "Итог",
        "kind_full": "полная обработка", "kind_summary": "саммари",
        "kind_analysis": "анализ", "kind_both": "саммари + анализ",
        "filter_all": "Все прогоны", "filter_error": "Только с ошибкой",
        "filter_done": "Только успешные",
        "refresh": "Обновить", "clear": "Очистить журнал", "close": "Закрыть",
        "clear_confirm_title": "Очистить журнал",
        "clear_confirm": "Удалить все записи истории обработки? Файлы результатов "
                         "и очередь не пострадают.",
        "empty": "Журнал пуст: здесь появится каждый запуск обработки или "
                 "перегенерации.",
        "pick": "Выберите прогон слева.",
        "interrupted": "Прервано",
        "running": "В работе",
        "h_run": "Прогон", "h_file": "Файл", "h_kind": "Что делали",
        "h_source": "Откуда встреча",
        "h_started": "Начало", "h_finished": "Конец", "h_duration": "Длительность",
        "h_status": "Итог", "h_engine": "Движок", "h_provider": "AI-провайдер",
        "h_events": "История статусов", "h_stages": "Этапы",
        "h_artifacts": "Создано", "h_error": "Ошибка",
        "art_summary": "саммари v{n}", "art_analysis": "анализ v{n}",
        "none": "—",
        "runs_n": "Прогонов: {n}",
    },
    "en": {
        "title": "Processing history",
        "hint": "Journal of runs. Removing a meeting from the queue leaves it intact.",
        "col_file": "File", "col_kind": "What ran", "col_started": "Started",
        "col_duration": "Duration", "col_status": "Outcome",
        "kind_full": "full processing", "kind_summary": "summary",
        "kind_analysis": "analysis", "kind_both": "summary + analysis",
        "filter_all": "All runs", "filter_error": "Failed only",
        "filter_done": "Successful only",
        "refresh": "Refresh", "clear": "Clear journal", "close": "Close",
        "clear_confirm_title": "Clear journal",
        "clear_confirm": "Delete every processing-history record? Produced files "
                         "and the queue are not affected.",
        "empty": "The journal is empty: every processing or regeneration run will "
                 "appear here.",
        "pick": "Select a run on the left.",
        "interrupted": "Interrupted",
        "running": "Running",
        "h_run": "Run", "h_file": "File", "h_kind": "What ran",
        "h_source": "Intake channel",
        "h_started": "Started", "h_finished": "Finished", "h_duration": "Duration",
        "h_status": "Outcome", "h_engine": "Engine", "h_provider": "AI provider",
        "h_events": "Status history", "h_stages": "Stages",
        "h_artifacts": "Produced", "h_error": "Error",
        "art_summary": "summary v{n}", "art_analysis": "analysis v{n}",
        "none": "—",
        "runs_n": "Runs: {n}",
    },
}


def _fmt_time(iso: str) -> str:
    """'2026-08-17T00:41:12' -> '17.08 00:41:12' (empty stays empty)."""
    iso = (iso or "").strip()
    if len(iso) < 19 or "T" not in iso:
        return iso
    date, clock = iso.split("T", 1)
    parts = date.split("-")
    if len(parts) != 3:
        return iso
    return f"{parts[2]}.{parts[1]} {clock[:8]}"


class HistoryDialog(QDialog):
    COL_FILE, COL_KIND, COL_STARTED, COL_DURATION, COL_STATUS = range(5)

    def __init__(self, run_store, language: str = "ru", parent=None):
        super().__init__(parent)
        self._store = run_store
        self._lang = language if language in _L else "ru"
        self._runs: list = []
        self.setWindowTitle(self._t("title"))
        self.resize(1180, 660)

        root = QVBoxLayout(self)
        head = QHBoxLayout()
        hint = QLabel(self._t("hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        self.cb_filter = QComboBox()
        for value, key in (("all", "filter_all"), ("error", "filter_error"),
                           ("done", "filter_done")):
            self.cb_filter.addItem(self._t(key), value)
        self.cb_filter.currentIndexChanged.connect(self._render)
        head.addWidget(self.cb_filter)
        root.addLayout(head)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        # Long file names and error texts must never widen the window: the file
        # column takes the slack and its text elides.
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        # A floor for every column, so the stretched file name never collapses to
        # a sliver when the other four are wide.
        header.setMinimumSectionSize(90)
        header.setSectionResizeMode(self.COL_FILE, QHeaderView.ResizeMode.Stretch)
        for column in (self.COL_KIND, self.COL_STARTED, self.COL_DURATION,
                       self.COL_STATUS):
            # Contents-sized, so the four short columns are always fully visible
            # and the file name — the only unbounded value — takes what is left.
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setHorizontalHeaderLabels([
            self._t("col_file"), self._t("col_kind"), self._t("col_started"),
            self._t("col_duration"), self._t("col_status")])
        self.table.itemSelectionChanged.connect(self._show_selected)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.table)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("hint")
        lv.addWidget(self.lbl_count)
        split.addWidget(left)

        self.details = QTextBrowser()
        self.details.setObjectName("runDetails")
        # The detail pane wraps: a 600-character provider error belongs on several
        # lines, not behind a horizontal scrollbar.
        self.details.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        split.addWidget(self.details)
        # Explicit split: the run list carries five columns and must not be
        # squeezed until "Итог" falls off the right edge.
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([700, 460])
        root.addWidget(split, 1)

        row = QHBoxLayout()
        b_refresh = QPushButton(self._t("refresh"))
        b_refresh.clicked.connect(self._render)
        b_clear = QPushButton(self._t("clear"))
        b_clear.clicked.connect(self._clear)
        b_close = QPushButton(self._t("close"))
        b_close.clicked.connect(self.accept)
        row.addWidget(b_refresh)
        row.addWidget(b_clear)
        row.addStretch(1)
        row.addWidget(b_close)
        root.addLayout(row)

        self._render()

    # -- helpers -------------------------------------------------------
    def _t(self, key: str) -> str:
        return _L[self._lang].get(key, key)

    def _status_label(self, raw: str) -> str:
        if raw == INTERRUPTED:
            return self._t("interrupted")
        if not raw:
            return self._t("running")
        if raw in JobStatus._value2member_map_:
            return main_label(JobStatus(raw), self._lang)
        return raw

    def _kind_label(self, kind: str) -> str:
        return self._t({"summary": "kind_summary",
                        "analysis": "kind_analysis",
                        "summary+analysis": "kind_both"}.get(kind, "kind_full"))

    def _duration_label(self, run: dict) -> str:
        from ..core.pipeline import fmt_duration
        seconds = float(run.get("durationSec") or 0.0)
        if not run.get("finishedAt"):
            return "…"
        return fmt_duration(seconds) if seconds else self._t("none")

    # -- rendering -----------------------------------------------------
    def _render(self) -> None:
        try:
            runs = list(self._store.load())
        except Exception:      # noqa: BLE001 - an unreadable journal is not a crash
            runs = []
        mode = self.cb_filter.currentData() or "all"
        if mode == "error":
            runs = [r for r in runs
                    if r.get("status") in (JobStatus.ERROR.value, INTERRUPTED)]
        elif mode == "done":
            runs = [r for r in runs if r.get("status") == JobStatus.DONE.value]
        runs.reverse()                       # newest first
        self._runs = runs
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for run in runs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = str(run.get("videoName") or run.get("videoPath") or "")
            # A live meeting runs the same stages a regenerated file does, so
            # "what ran" cannot tell them apart — the channel is what does.
            badge = source_badge(run.get("source"))
            item = QTableWidgetItem(f"{badge}  {name}" if badge else name)
            item.setToolTip(
                f"{source_label(run.get('source'), self._lang)}\n"
                f"{run.get('videoPath') or name}")
            self.table.setItem(row, self.COL_FILE, item)
            self.table.setItem(row, self.COL_KIND,
                               QTableWidgetItem(self._kind_label(str(run.get("kind", "")))))
            self.table.setItem(row, self.COL_STARTED,
                               QTableWidgetItem(_fmt_time(str(run.get("startedAt", "")))))
            self.table.setItem(row, self.COL_DURATION,
                               QTableWidgetItem(self._duration_label(run)))
            self.table.setItem(row, self.COL_STATUS,
                               QTableWidgetItem(self._status_label(str(run.get("status", "")))))
        self.table.blockSignals(False)
        self.lbl_count.setText(self._t("runs_n").format(n=len(runs)))
        if runs:
            self.table.selectRow(0)
        else:
            self.details.setHtml(f"<p>{escape(self._t('empty'))}</p>")

    def _show_selected(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            self.details.setHtml(f"<p>{escape(self._t('pick'))}</p>")
            return
        index = min(rows)
        if not (0 <= index < len(self._runs)):
            return
        self.details.setHtml(self._render_run(self._runs[index]))

    def _render_run(self, run: dict) -> str:
        from ..core.pipeline import fmt_duration

        def row(key: str, value: str) -> str:
            if not value:
                return ""
            return (f"<tr><td style='padding:2px 14px 2px 0;white-space:nowrap'>"
                    f"{escape(self._t(key))}</td><td>{escape(value)}</td></tr>")

        head = "".join([
            row("h_file", str(run.get("videoName", ""))),
            row("h_kind", self._kind_label(str(run.get("kind", "")))),
            row("h_source", source_label(run.get("source"), self._lang)),
            row("h_started", _fmt_time(str(run.get("startedAt", "")))),
            row("h_finished", _fmt_time(str(run.get("finishedAt", "")))),
            row("h_duration", self._duration_label(run)),
            row("h_status", self._status_label(str(run.get("status", "")))),
            row("h_engine", str(run.get("engine", ""))),
            row("h_provider", str(run.get("provider", ""))),
        ])
        parts = [f"<table>{head}</table>"]

        events = run.get("events") or []
        if events:
            lines = "".join(
                f"<tr><td style='padding:1px 12px 1px 0;white-space:nowrap'>"
                f"{escape(_fmt_time(str(e.get('at', ''))))}</td>"
                f"<td>{escape(self._status_label(str(e.get('status', ''))))}</td></tr>"
                for e in events)
            parts.append(f"<h4 style='margin:12px 0 4px'>{escape(self._t('h_events'))}</h4>"
                         f"<table>{lines}</table>")

        stages = run.get("stages") or []
        if stages:
            lines = "".join(
                f"<tr><td style='padding:1px 12px 1px 0'>{escape(str(s.get('label', '')))}</td>"
                f"<td style='white-space:nowrap'>{escape(fmt_duration(float(s.get('seconds') or 0)))}</td></tr>"
                for s in stages)
            parts.append(f"<h4 style='margin:12px 0 4px'>{escape(self._t('h_stages'))}</h4>"
                         f"<table>{lines}</table>")

        artifacts = run.get("artifacts") or []
        if artifacts:
            names = [self._t("art_analysis" if a.get("kind") == "analysis" else "art_summary")
                     .format(n=a.get("version", "?")) for a in artifacts]
            parts.append(f"<h4 style='margin:12px 0 4px'>{escape(self._t('h_artifacts'))}</h4>"
                         f"<p>{escape(', '.join(names))}</p>")

        error = str(run.get("error") or "").strip()
        if error:
            # Escaped and pre-wrapped: provider errors quote the model's own output,
            # angle brackets included, and must not be read as markup.
            parts.append(f"<h4 style='margin:12px 0 4px'>{escape(self._t('h_error'))}</h4>"
                         f"<div style='white-space:pre-wrap'>{escape(error)}</div>")
        return "".join(parts)

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self, self._t("clear_confirm_title"), self._t("clear_confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            self._store.clear()
        except Exception as exc:      # noqa: BLE001
            QMessageBox.warning(self, self._t("title"), str(exc))
            return
        self._render()
